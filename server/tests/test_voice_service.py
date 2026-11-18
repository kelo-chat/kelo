"""The voice container's program (voice_service.py): text joining, tags, idle unload, refusal without ready.json, and the socket
routes, with a fake recognizer and a fake VAD (no real model in unit tests)."""

import json
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
            assert (await c.get("/health")).json() == {"ok": True, "loaded": None, "busy": False}
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
