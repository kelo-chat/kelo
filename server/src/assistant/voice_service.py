"""The program of the `voice` container (ROLE=voice): speech to text with a model the server downloaded (voice_download.py).

It imports only the standard library, sherpa_onnx and numpy, not the rest of `assistant`. It listens on a Unix socket in the
voice-io volume and answers three routes:

    GET  /health                                  -> {"ok", "loaded": model id | null, "busy"}
    POST /transcribe  (X-Kelo-Voice-Model, X-Kelo-Voice-Lang; body = PCM16 mono 16 kHz) -> {"text", "lang", "segments", "took_ms"}
    POST /unload                                  -> {"ok": true}

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
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿　-〿＀-￯]")
TAG = re.compile(r"<\|[^|>]*\|>")

# the files each model folder must hold (names only; sizes and hashes were checked when the server downloaded them)
MODEL_FILES: dict[str, tuple[str, ...]] = {
    "sensevoice-small": ("model.int8.onnx", "tokens.txt"),
    "parakeet-tdt-v3": ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
}


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
        prev_end = 0

        def decode_ready() -> None:
            nonlocal lang, segments, prev_end
            while not vad.empty():
                seg = vad.front
                start = getattr(seg, "start", None)
                own = list(seg.samples)   # read before pop(): the segment's buffer goes with it
                vad.pop()
                if start is None:
                    chunk = own
                else:
                    # The model writes spaced-out syllables when a segment begins right at speech (measured with the real
                    # SenseVoice model: 0.8 s of audio before the speech gives clean text). So each segment gets up to one
                    # second of the audio before it (never past the previous segment).
                    lead = max(0, min(LEAD_SAMPLES, start - prev_end))
                    chunk = samples[start - lead:start + len(own) + TAIL_SAMPLES].tolist()
                    prev_end = start + len(own)
                stream = self._model.create_stream()
                stream.accept_waveform(SAMPLE_RATE, chunk)
                self._model.decode_stream(stream)
                result = stream.result
                segments += 1
                if clean_text(result.text):
                    texts.append(clean_text(result.text))
                lang = lang or lang_code(getattr(result, "lang", ""))
                del stream

        for i in range(0, len(samples), VAD_WINDOW):
            vad.accept_waveform(samples[i:i + VAD_WINDOW])
            decode_ready()
        vad.flush()
        decode_ready()
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


def make_handler(engine: Engine) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await _read_request(reader)
            if req is None:
                return
            method, path, headers, body = req
            if method == "GET" and path == "/health":
                _reply(writer, 200, {"ok": True, "loaded": engine.loaded_id, "busy": engine.lock.locked()})
            elif method == "POST" and path == "/unload":
                if not engine.lock.locked():
                    engine.unload()
                _reply(writer, 200, {"ok": True})
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


async def serve(socket_path: str, root: Path, idle_seconds: float, threads: int, silero_path: str) -> None:
    engine = Engine(root, silero_path, threads, idle_seconds)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(make_handler(engine), path=socket_path)
    os.chmod(socket_path, 0o660)
    asyncio.get_running_loop().create_task(engine.idle_watch())
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
    asyncio.run(serve(socket_path, root, idle, threads, silero))


if __name__ == "__main__":
    main()
