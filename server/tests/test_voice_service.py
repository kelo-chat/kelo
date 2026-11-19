"""The voice container's program (voice_service.py): text joining, tags, idle unload, refusal without ready.json, and the socket
routes, with a fake recognizer and a fake VAD (no real model in unit tests)."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from assistant import voice_service as vs


def test_join_segments_spaces_latin_and_hangul_but_not_cjk():
    assert vs.join_segments(["안녕하세요", "Kelo 입니다", "hello"]) == "안녕하세요 Kelo 입니다 hello"
    assert vs.join_segments(["今日は", "いい天気です"]) == "今日はいい天気です"
    assert vs.join_segments(["我们去", "公园吧"]) == "我们去公园吧"
    assert vs.join_segments(["", "  ", "one"]) == "one"


def test_tags_are_stripped_and_the_language_is_read():
    assert vs.clean_text("<|ko|><|NEUTRAL|><|withitn|>안녕하세요.") == "안녕하세요."
    assert vs.lang_code("<|ko|>") == "ko" and vs.lang_code("") == ""


def test_model_refused_without_ready_json(tmp_path: Path):
    folder = tmp_path / "sensevoice-small"
    folder.mkdir()
    (folder / "model.int8.onnx").write_bytes(b"x")
    (folder / "tokens.txt").write_bytes(b"y")
    with pytest.raises(RuntimeError):
        vs.ready_or_raise(tmp_path, "sensevoice-small")
    (folder / "ready.json").write_text(json.dumps({"files": {"model.int8.onnx": "a", "tokens.txt": "b"}}))
    assert vs.ready_or_raise(tmp_path, "sensevoice-small") == folder


def test_unknown_model_id_is_refused(tmp_path: Path):
    with pytest.raises(ValueError):
        vs.model_dir(tmp_path, "../etc")


def test_idle_clock_with_a_fake_clock():
    now = [100.0]
    clock = vs.IdleClock(600, now=lambda: now[0])
    assert not clock.expired()
    now[0] += 599
    assert not clock.expired()
    now[0] += 2
    assert clock.expired()
    clock.touch()
    assert not clock.expired()


class FakeVAD:
    """Splits the input into one segment per 1-second run of non-zero samples; the segments are the samples themselves."""

    def __init__(self):
        self.pending: list = []
        self.buf: list = []
        self.reset_calls = 0

    def accept_waveform(self, samples):
        self.buf.extend(samples.tolist())
        while len(self.buf) >= 16000:
            self.pending.append(SimpleNamespace(samples=self.buf[:16000]))
            self.buf = self.buf[16000:]

    def empty(self):
        return not self.pending

    @property
    def front(self):
        return self.pending[0]

    def pop(self):
        self.pending.pop(0)

    def flush(self):
        if self.buf:
            self.pending.append(SimpleNamespace(samples=self.buf))
            self.buf = []

    def reset(self):
        self.reset_calls += 1


class FakeRecognizer:
    def __init__(self, texts):
        self.texts = list(texts)

    def create_stream(self):
        outer = self

        class Stream:
            def accept_waveform(self, rate, samples):
                assert rate == 16000 and len(samples) > 0

            @property
            def result(self):
                return SimpleNamespace(text=outer.texts.pop(0), lang="<|ko|>")
        return Stream()

    def decode_stream(self, stream):
        return None


def engine_for(root: Path, texts=("<|ko|>안녕하세요", "<|ko|>Kelo")):
    seen = {}

    def factory(folder, model_id, threads, hint):
        seen.update(model=model_id, hint=hint, threads=threads)
        return FakeRecognizer(texts)

    eng = vs.Engine(root, "/nowhere/silero.onnx", 2, 600, recognizer_factory=factory, vad_factory=lambda p: FakeVAD())
    return eng, seen


def ready_folder(root: Path, model_id: str = "sensevoice-small") -> None:
    folder = root / model_id
    folder.mkdir(parents=True)
    names = vs.MODEL_FILES[model_id]
    for n in names:
        (folder / n).write_bytes(b"x")
    (folder / "ready.json").write_text(json.dumps({"files": {n: "0" for n in names}}))


async def test_engine_joins_segments_and_passes_the_hint(tmp_path):
    ready_folder(tmp_path)
    eng, seen = engine_for(tmp_path)
    pcm = (b"\x01\x00" * 16000) * 2      # two seconds of non-zero samples -> two segments
    out = await eng.transcribe("sensevoice-small", "ko", pcm)
    assert out["text"] == "안녕하세요 Kelo" and out["lang"] == "ko" and out["segments"] == 2
    assert seen == {"model": "sensevoice-small", "hint": "ko", "threads": 2}
    assert out["took_ms"] >= 0


async def test_socket_routes_round_trip(tmp_path):
    import asyncio
    import shutil
    import tempfile
    ready_folder(tmp_path)
    eng, _ = engine_for(tmp_path)
    short = Path(tempfile.mkdtemp(prefix="vs"))          # Unix socket paths are short (104 bytes on macOS)
    sock = str(short / "v.sock")
    server = await asyncio.start_unix_server(vs.make_handler(eng), path=sock)
    try:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=sock), base_url="http://voice") as c:
            assert (await c.get("/health")).json() == {"ok": True, "loaded": None, "busy": False,
                                                       "speaking": False, "speech_loaded": False}
            r = await c.post("/transcribe", content=b"\x01\x00" * 16000,
                             headers={"X-Kelo-Voice-Model": "sensevoice-small", "X-Kelo-Voice-Lang": "ko"})
            assert r.status_code == 200 and r.json()["text"] == "안녕하세요"
            assert (await c.get("/health")).json()["loaded"] == "sensevoice-small"
            r = await c.post("/transcribe", content=b"\x01\x00" * 16000, headers={"X-Kelo-Voice-Model": "parakeet-tdt-v3"})
            assert r.status_code == 409 and r.json() == {"code": "voice_not_ready"}
            r = await c.post("/transcribe", content=b"\x00\x00" * (vs.MAX_PCM_BYTES // 2 + 10),
                             headers={"X-Kelo-Voice-Model": "sensevoice-small"})
            assert r.status_code == 413 and r.json() == {"code": "voice_too_long"}
            assert (await c.post("/unload")).json() == {"ok": True}
            assert (await c.get("/health")).json()["loaded"] is None
            assert (await c.get("/nope")).status_code == 404
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(short, ignore_errors=True)


class StartVAD(FakeVAD):
    """Segments that carry their start sample, as sherpa's do."""

    def accept_waveform(self, samples):
        self.buf.extend(samples.tolist())
        self.fed = getattr(self, "fed", 0) + len(samples)

    def flush(self):
        # two segments: [4000, 20000) and [30000, 40000) of the whole input
        self.pending = [SimpleNamespace(samples=[0.1] * 16000, start=4000),
                        SimpleNamespace(samples=[0.2] * 10000, start=30000)]


async def test_each_segment_gets_up_to_one_second_before_it_and_never_overlaps_the_previous(tmp_path):
    ready_folder(tmp_path)
    seen: list[int] = []

    class Rec(FakeRecognizer):
        def create_stream(self):
            stream = super().create_stream()
            original = stream.accept_waveform

            def accept(rate, samples):
                seen.append(len(samples))
                original(rate, samples)
            stream.accept_waveform = accept
            return stream

    eng = vs.Engine(tmp_path, "/x.onnx", 1, 600, recognizer_factory=lambda *a: Rec(["<|ko|>a", "<|ko|>b"]),
                    vad_factory=lambda p: StartVAD())
    pcm = (b"\x01\x00" * 40000)
    out = await eng.transcribe("sensevoice-small", None, pcm)
    assert out["segments"] == 2
    # first: 4000 samples of lead (the whole lead the input has before start) + 16000 own
    # second: lead is min(16000, 30000 - 20000 = 10000) = 10000 + 10000 own
    assert seen == [4000 + 16000, 10000 + 10000]


# ---- Kelo's voice (POST /speak, the Speaker): a fake TTS, no real model in unit tests

class FakeTTS:
    def __init__(self, samples=(0.0, 0.5, -1.5, 1.2), rate=44100, gate=None):
        self.samples = list(samples)
        self.rate = rate
        self.gate = gate              # a threading.Event the generate call waits for (to show two locks at once)
        self.calls = []

    def generate(self, text, sid, speed, lang, steps):
        self.calls.append((text, sid, speed, lang, steps))
        if self.gate is not None:
            self.gate.wait(5)
        return self.samples, self.rate


def speech_folder(root: Path) -> None:
    ready_folder(root, vs.SPEECH_ID)


def speaker_for(root: Path, tts=None, now=time.monotonic):
    made = []
    tts = tts or FakeTTS()

    def factory(folder, threads):
        made.append((folder, threads))
        return tts

    return vs.Speaker(root, 2, 600, tts_factory=factory, now=now), tts, made


SPEAK_HEADERS = {"X-Kelo-Speech-Voice": "3", "X-Kelo-Speech-Speed": "1.2", "X-Kelo-Speech-Lang": "ko", "X-Kelo-Speech-Steps": "8"}


def test_to_pcm16_clips_and_is_little_endian():
    assert vs.to_pcm16([0.0, 0.5, -1.5, 1.2]) == b"\x00\x00" + b"\x00\x40" + b"\x01\x80" + b"\xff\x7f"


def test_speech_languages_are_the_same_list_as_the_server_side_catalogue():
    from assistant import voice_models as vm
    assert vs.SPEAK_LANGS == vm.SPEAK_LANGS


def test_parse_speak_refuses_bad_arguments():
    assert vs.parse_speak({k.lower(): v for k, v in SPEAK_HEADERS.items()}, b"hi")[1:] == (3, 1.2, "ko", 8)
    for bad in [{"x-kelo-speech-voice": "10"}, {"x-kelo-speech-speed": "3"}, {"x-kelo-speech-lang": "zh"},
                {"x-kelo-speech-steps": "0"}, {"x-kelo-speech-voice": "x"}]:
        headers = {**{k.lower(): v for k, v in SPEAK_HEADERS.items()}, **bad}
        with pytest.raises(vs.SpeakArgsError):
            vs.parse_speak(headers, b"hi")
    with pytest.raises(vs.SpeakArgsError):
        vs.parse_speak({k.lower(): v for k, v in SPEAK_HEADERS.items()}, b"")
    with pytest.raises(vs.SpeakArgsError):
        vs.parse_speak({k.lower(): v for k, v in SPEAK_HEADERS.items()}, b"x" * (vs.MAX_SPEAK_BYTES + 1))


async def test_speaker_returns_pcm_at_the_model_rate(tmp_path):
    speech_folder(tmp_path)
    spk, tts, made = speaker_for(tmp_path)
    pcm, rate, took = await spk.speak("안녕하세요", 3, 1.2, "ko", 8)
    assert rate == 44100 and took >= 0
    assert pcm == vs.to_pcm16([0.0, 0.5, -1.5, 1.2])
    assert tts.calls == [("안녕하세요", 3, 1.2, "ko", 8)]
    assert made == [(tmp_path / "supertonic-3", 2)] and spk.loaded is True


async def test_speak_without_ready_json_is_not_ready(tmp_path):
    spk, _, _ = speaker_for(tmp_path)
    with pytest.raises(RuntimeError):
        await spk.speak("hi", 0, 1.0, "en", 8)
    assert spk.loaded is False


async def test_speak_route_returns_wav_bytes_and_the_rate(tmp_path):
    import asyncio
    import shutil
    import tempfile
    speech_folder(tmp_path)
    ready_folder(tmp_path)
    eng, _ = engine_for(tmp_path)
    spk, _, _ = speaker_for(tmp_path)
    short = Path(tempfile.mkdtemp(prefix="vs"))
    sock = str(short / "v.sock")
    server = await asyncio.start_unix_server(vs.make_handler(eng, spk), path=sock)
    try:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=sock), base_url="http://voice") as c:
            r = await c.post("/speak", content="안녕하세요".encode(), headers=SPEAK_HEADERS)
            assert r.status_code == 200 and r.content == vs.to_pcm16([0.0, 0.5, -1.5, 1.2])
            assert r.headers["X-Kelo-Speech-Rate"] == "44100" and "X-Kelo-Took-Ms" in r.headers
            r = await c.post("/speak", content=b"hi", headers={**SPEAK_HEADERS, "X-Kelo-Speech-Voice": "10"})
            assert r.status_code == 400 and r.json() == {"code": "voice_speech_args"}
            r = await c.post("/speak", content=b"hi", headers={**SPEAK_HEADERS, "X-Kelo-Speech-Speed": "3"})
            assert r.status_code == 400
            r = await c.post("/speak", content=b"hi", headers={**SPEAK_HEADERS, "X-Kelo-Speech-Lang": "zh"})
            assert r.status_code == 400
            r = await c.post("/speak", content=b"hi", headers={**SPEAK_HEADERS, "X-Kelo-Speech-Steps": "0"})
            assert r.status_code == 400
            assert (await c.get("/health")).json()["speech_loaded"] is True
            assert (await c.post("/unload")).json() == {"ok": True}
            assert (await c.get("/health")).json()["speech_loaded"] is False
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(short, ignore_errors=True)


async def test_speak_route_without_ready_model_is_409(tmp_path):
    import asyncio
    import shutil
    import tempfile
    ready_folder(tmp_path)
    eng, _ = engine_for(tmp_path)
    spk, _, _ = speaker_for(tmp_path)
    short = Path(tempfile.mkdtemp(prefix="vs"))
    sock = str(short / "v.sock")
    server = await asyncio.start_unix_server(vs.make_handler(eng, spk), path=sock)
    try:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=sock), base_url="http://voice") as c:
            r = await c.post("/speak", content=b"hi", headers=SPEAK_HEADERS)
            assert r.status_code == 409 and r.json() == {"code": "voice_speech_not_ready"}
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(short, ignore_errors=True)


def test_idle_unload_drops_both_models_with_a_fake_clock(tmp_path):
    ready_folder(tmp_path)
    speech_folder(tmp_path)
    now = [100.0]
    eng, _ = engine_for(tmp_path)
    eng.idle = vs.IdleClock(600, now=lambda: now[0])
    spk, _, _ = speaker_for(tmp_path, now=lambda: now[0])
    spk.idle = vs.IdleClock(600, now=lambda: now[0])
    eng.loaded_id = "sensevoice-small"
    spk._tts = FakeTTS()
    spk.loaded = True
    vs.unload_idle(eng, spk)
    assert eng.loaded_id == "sensevoice-small" and spk.loaded       # not idle long enough yet
    now[0] += 601
    vs.unload_idle(eng, spk)
    assert eng.loaded_id is None and spk.loaded is False


async def test_a_transcription_and_a_sentence_run_side_by_side(tmp_path):
    import threading
    ready_folder(tmp_path)
    speech_folder(tmp_path)
    eng, _ = engine_for(tmp_path)
    gate = threading.Event()
    spk, _, _ = speaker_for(tmp_path, tts=FakeTTS(gate=gate))
    speaking = asyncio.ensure_future(spk.speak("hello", 0, 1.0, "en", 8))
    await asyncio.sleep(0.05)
    assert spk.lock.locked() and not speaking.done()
    out = await asyncio.wait_for(eng.transcribe("sensevoice-small", "ko", b"\x01\x00" * 16000), 5)
    assert out["text"]                      # the transcription finished while the sentence still waits
    gate.set()
    await asyncio.wait_for(speaking, 5)


async def test_spoken_text_never_reaches_the_log(tmp_path, caplog):
    speech_folder(tmp_path)
    spk, _, _ = speaker_for(tmp_path)
    with caplog.at_level("DEBUG"):
        await spk.speak("비밀 문장 Secret sentence 42", 1, 1.0, "ko", 8)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "chars=" in text and "비밀" not in text and "Secret" not in text
