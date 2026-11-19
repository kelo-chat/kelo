"""Kelo's voice (api/voice.py speech routes, voice_speak.py): states, prefs, the speak limits, busy, timeout, privacy.
A fake downloader and a fake voice container; no real model in unit tests."""

import asyncio
import io
import logging
import wave

import httpx
import pytest
from fastapi import FastAPI, Request

from assistant import voice_models as vm
from assistant import voice_speak as vsp
from assistant.api import voice as api_voice
from assistant.api.context import AppContext
from assistant.spoken_numbers import SpokenNumbers
from assistant.store import Store
from assistant.voice import Voice
from assistant.voice_download import DownloadError

from test_voice_api import FakeClient, FakeDownloader

SUPER = vm.SUPERTONIC_ID


class FakeSpeechDownloader(FakeDownloader):
    def __init__(self, fail: str | None = None):
        super().__init__()
        self.fail = fail

    async def fetch(self, model, on_progress):
        self.fetched.append(model.id)
        if self.fail:
            raise DownloadError(self.fail)
        if self.gate is not None:
            await self.gate.wait()
        self.ready_ids.add(model.id)


class FakeSpeechClient:
    def __init__(self, delay=0.0, up=True, raises=None, pcm=None, rate=44100):
        self.delay, self.up, self.raises = delay, up, raises
        self.pcm = pcm if pcm is not None else (b"\x00\x40" * 2000)
        self.rate = rate
        self.calls: list[tuple] = []

    async def health(self):
        if not self.up:
            raise ConnectionError("no voice container")
        return {"ok": True, "loaded": None, "busy": False, "speaking": False, "speech_loaded": False}

    async def speak(self, text, voice, speed, lang, steps, timeout_s):
        self.calls.append((text, voice, speed, lang, steps))
        if self.raises:
            raise self.raises
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.pcm, self.rate

    async def transcribe(self, model_id, lang, pcm16, timeout_s):
        return {"text": "", "lang": "", "segments": 0, "took_ms": 0}

    async def unload(self):
        return None


def build(tmp_path, client=None, downloader=None, meta=None):
    store = Store(":memory:")
    for k, v in (meta or {}).items():
        store.set_meta(k, v)
    dl = downloader or FakeSpeechDownloader()
    client = client or FakeClient()
    voice = Voice(store, dl, client)
    speech = client if isinstance(client, FakeSpeechClient) else FakeSpeechClient()
    out = vsp.VoiceOut(store, dl, speech, SpokenNumbers())
    app = FastAPI()

    def no_auth(request: Request) -> None:
        return None

    ctx = AppContext(voice=voice, voice_out=out, require_auth=no_auth, store=store)
    app.include_router(api_voice.router(ctx))
    return app, out, dl, speech, store


def http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test")


def ready(dl):
    dl.ready_ids.add(SUPER)


async def test_states_offered_downloading_ready_off_and_failed(tmp_path):
    dl = FakeSpeechDownloader()
    dl.gate = asyncio.Event()
    app, out, *_ = build(tmp_path, downloader=dl)
    async with http(app) as c:
        v = (await c.get("/api/voice/speech")).json()
        assert v["state"] == "offered" and v["declined"] is False and v["runtime"] == "ready"
        assert v["voices"][0] == {"id": 0, "kind": "female", "n": 1} and v["voices"][9] == {"id": 9, "kind": "male", "n": 5}
        r = await c.post("/api/voice/speech/model")
        assert r.status_code == 202 and r.json()["state"] == "downloading"
        dl.gate.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not out.job.running():
                break
        assert (await c.get("/api/voice/speech")).json()["state"] == "ready"
        assert (await c.delete("/api/voice/speech/model")).json()["state"] == "offered"
    dl2 = FakeSpeechDownloader(fail="offline")
    app2, out2, *_ = build(tmp_path, downloader=dl2)
    async with http(app2) as c:
        await c.post("/api/voice/speech/model")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not out2.job.running():
                break
        v = (await c.get("/api/voice/speech")).json()
        assert v["state"] == "failed" and v["error"] == "offline"
    app3, *_ = build(tmp_path)
    async with http(app3) as c:
        assert (await c.post("/api/voice/speech/decline")).json()["state"] == "off"


async def test_runtime_missing_is_reported(tmp_path):
    app, *_ = build(tmp_path, client=FakeSpeechClient(up=False))
    async with http(app) as c:
        assert (await c.get("/api/voice/speech")).json()["runtime"] == "missing"


async def test_prefs_are_kept_and_checked(tmp_path):
    app, out, _, _, store = build(tmp_path)
    async with http(app) as c:
        v = (await c.put("/api/voice/speech/prefs", json={"voice": 3, "speed": 1.2})).json()
        assert v["voice"] == 3 and v["speed"] == 1.2
        assert store.meta("speech_voice") == "3"
        assert (await c.put("/api/voice/speech/prefs", json={"voice": 10})).status_code == 422
        r = await c.put("/api/voice/speech/prefs", json={"speed": 3})
        assert r.status_code == 422 and r.json() == {"code": "voice_speech_args"}
        assert (await c.put("/api/voice/speech/prefs", json={"speed": 0.69})).status_code == 422


async def test_speak_returns_a_wav_at_44100_mono_16bit(tmp_path):
    dl = FakeSpeechDownloader()
    ready(dl)
    app, out, _, speech, store = build(tmp_path, downloader=dl)
    store.set_meta("speech_model", SUPER)
    async with http(app) as c:
        r = await c.post("/api/voice/speak", json={"text": "안녕하세요", "lang": "ko"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert "X-Kelo-Seconds" in r.headers and "X-Kelo-Took-Ms" in r.headers
        with wave.open(io.BytesIO(r.content), "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 44100)
    assert speech.calls == [("안녕하세요", 0, 1.0, "ko", 8)]


async def test_speak_applies_the_saved_prefs_and_the_listen_override(tmp_path):
    dl = FakeSpeechDownloader()
    ready(dl)
    app, out, _, speech, store = build(tmp_path, downloader=dl, meta={"speech_model": SUPER, "speech_voice": "4", "speech_speed": "1.3"})
    async with http(app) as c:
        await c.post("/api/voice/speak", json={"text": "hi", "lang": "en"})
        await c.post("/api/voice/speak", json={"text": "hi", "lang": "en", "voice": 7, "speed": 0.8})
    assert speech.calls[0][1:3] == (4, 1.3) and speech.calls[1][1:3] == (7, 0.8)


async def test_speak_refusals_have_their_codes(tmp_path):
    dl = FakeSpeechDownloader()
    app, out, _, speech, store = build(tmp_path, downloader=dl)
    async with http(app) as c:
        r = await c.post("/api/voice/speak", json={"text": "hi", "lang": "en"})
        assert r.status_code == 409 and r.json() == {"code": "voice_speech_not_ready"}
    ready(dl)
    store.set_meta("speech_model", SUPER)
    async with http(app) as c:
        r = await c.post("/api/voice/speak", json={"text": "x" * 1001, "lang": "en"})
        assert r.status_code == 413 and r.json() == {"code": "voice_speech_too_long"}
        r = await c.post("/api/voice/speak", json={"text": "hi", "lang": "zh"})
        assert r.status_code == 422 and r.json() == {"code": "voice_speech_lang"}
        r = await c.post("/api/voice/speak", json={"text": "   ", "lang": "en"})
        assert r.status_code == 422 and r.json() == {"code": "voice_speech_empty"}


async def test_six_at_once_gets_a_busy_answer(tmp_path):
    dl = FakeSpeechDownloader()
    ready(dl)
    app, *_ = build(tmp_path, downloader=dl, client=FakeSpeechClient(delay=0.2), meta={"speech_model": SUPER})
    async with http(app) as c:
        rs = await asyncio.gather(*[c.post("/api/voice/speak", json={"text": f"hi {i}", "lang": "en"}) for i in range(6)])
    codes = [r.status_code for r in rs]
    assert 429 in codes and codes.count(200) >= 1
    assert {r.json()["code"] for r in rs if r.status_code == 429} == {"voice_busy"}


async def test_a_slow_container_is_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(vsp, "SPEAK_TIMEOUT_S", 0.05)
    dl = FakeSpeechDownloader()
    ready(dl)
    app, *_ = build(tmp_path, downloader=dl, client=FakeSpeechClient(delay=2.0), meta={"speech_model": SUPER})
    async with http(app) as c:
        r = await c.post("/api/voice/speak", json={"text": "hi", "lang": "en"})
    assert r.status_code == 504 and r.json() == {"code": "voice_timeout"}


async def test_a_container_that_is_down_is_503(tmp_path):
    dl = FakeSpeechDownloader()
    ready(dl)
    app, *_ = build(tmp_path, downloader=dl, client=FakeSpeechClient(raises=ConnectionError("down")), meta={"speech_model": SUPER})
    async with http(app) as c:
        r = await c.post("/api/voice/speak", json={"text": "hi", "lang": "en"})
    assert r.status_code == 503 and r.json() == {"code": "voice_runtime_missing"}


async def test_the_container_receives_the_text_after_the_number_rules(tmp_path):
    dl = FakeSpeechDownloader()
    ready(dl)
    app, out, _, speech, store = build(tmp_path, downloader=dl, meta={"speech_model": SUPER})
    async with http(app) as c:
        await c.post("/api/voice/speak", json={"text": "It takes 2.5 hours at 14:55", "lang": "en"})
    assert speech.calls[0][0] == "It takes two point five hours at fourteen fifty-five"


async def test_privacy_nothing_is_written_and_the_text_is_not_logged(tmp_path, caplog):
    dl = FakeSpeechDownloader()
    ready(dl)
    app, *_ = build(tmp_path, downloader=dl, meta={"speech_model": SUPER})
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    with caplog.at_level(logging.DEBUG):
        async with http(app) as c:
            r = await c.post("/api/voice/speak", json={"text": "비밀 문장 Secret sentence", "lang": "ko"})
    assert r.status_code == 200
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "chars=" in logs and "비밀" not in logs and "Secret" not in logs
