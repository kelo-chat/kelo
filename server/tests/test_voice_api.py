"""The voice API (api/voice.py) with a fake downloader and a fake voice container: states, limits, busy, timeout, privacy."""

import asyncio
import io
import logging
import os
import wave

import httpx
import pytest
from fastapi import FastAPI, Request

from assistant import voice as voice_mod
from assistant import voice_models as vm
from assistant.api import voice as api_voice
from assistant.api.context import AppContext
from assistant.store import Store
from assistant.voice import Voice


def wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(seconds * rate) * channels)
    return buf.getvalue()


class FakeDownloader:
    def __init__(self):
        self.ready_ids: set[str] = set()
        self.gate: asyncio.Event | None = None
        self.fetched: list[str] = []

    def ready(self, model) -> bool:
        return model.id in self.ready_ids

    async def fetch(self, model, on_progress):
        self.fetched.append(model.id)
        if self.gate is not None:
            await self.gate.wait()
        self.ready_ids.add(model.id)

    def remove(self, model_id: str) -> None:
        self.ready_ids.discard(model_id)


class FakeClient:
    def __init__(self, text="<|ko|><|NEUTRAL|>안녕하세요 Kelo", delay=0.0, up=True, raises=None):
        self.text, self.delay, self.up, self.raises = text, delay, up, raises
        self.calls: list[tuple] = []

    async def health(self):
        if not self.up:
            raise ConnectionError("no voice container")
        return {"ok": True, "loaded": None, "busy": False}

    async def transcribe(self, model_id, lang, pcm16, timeout_s):
        self.calls.append((model_id, lang, len(pcm16)))
        if self.raises:
            raise self.raises
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"text": self.text, "lang": "ko", "segments": 1, "took_ms": 5}

    async def unload(self):
        return None


def build(tmp_path, client=None, downloader=None, converter=None, meta=None):
    store = Store(":memory:")
    for k, v in (meta or {}).items():
        store.set_meta(k, v)
    dl = downloader or FakeDownloader()
    client = client or FakeClient()
    voice = Voice(store, dl, client, converter=converter)
    app = FastAPI()
    def no_auth(request: Request) -> None:
        return None

    ctx = AppContext(voice=voice, require_auth=no_auth, store=store)
    app.include_router(api_voice.router(ctx))
    return app, voice, dl, client, store


def http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test")


async def test_offered_for_a_supported_language_until_answered(tmp_path):
    app, *_ = build(tmp_path)
    async with http(app) as c:
        v = (await c.get("/api/voice", params={"lang": "ko"})).json()
        assert v["state"] == "offered" and v["recommended"]["id"] == vm.SENSE_ID
        assert v["runtime"] == "ready" and v["max_seconds"] == 120
        assert len(v["models"]) == 2
        assert (await c.post("/api/voice/decline")).json()["state"] == "off"
        assert (await c.get("/api/voice", params={"lang": "ko"})).json()["declined"] is True


async def test_unsupported_language_has_no_prompt(tmp_path):
    app, *_ = build(tmp_path)
    async with http(app) as c:
        v = (await c.get("/api/voice", params={"lang": "tr"})).json()
        assert v["state"] == "unsupported" and v["recommended"] is None


async def test_runtime_missing_is_reported(tmp_path):
    app, *_ = build(tmp_path, client=FakeClient(up=False))
    async with http(app) as c:
        assert (await c.get("/api/voice", params={"lang": "en"})).json()["runtime"] == "missing"


async def test_owner_language_beats_the_request_only_when_chosen(tmp_path):
    app, voice, *_ = build(tmp_path, meta={"ui_lang": "de", "ui_lang_from": "app"})
    assert voice.owner_language("ko") == "de"
    app2, voice2, *_ = build(tmp_path, meta={"ui_lang": "de"})   # no ui_lang_from: the request's language counts
    assert voice2.owner_language("ko") == "ko"


async def test_choose_downloads_then_is_ready(tmp_path):
    dl = FakeDownloader()
    app, voice, *_ = build(tmp_path, downloader=dl)
    async with http(app) as c:
        r = await c.post("/api/voice/model", json={"model": vm.PARAKEET_ID}, params={"lang": "de"})
        assert r.status_code in (200, 202)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert dl.fetched == [vm.PARAKEET_ID]
        v = (await c.get("/api/voice", params={"lang": "de"})).json()
        assert v["state"] == "ready" and v["model"]["id"] == vm.PARAKEET_ID


async def test_downloading_state_and_202(tmp_path):
    dl = FakeDownloader()
    dl.gate = asyncio.Event()
    app, *_ = build(tmp_path, downloader=dl)
    async with http(app) as c:
        r = await c.post("/api/voice/model", json={"model": vm.SENSE_ID}, params={"lang": "ko"})
        assert r.status_code == 202
        assert (await c.get("/api/voice", params={"lang": "ko"})).json()["state"] == "downloading"
        dl.gate.set()
        await asyncio.sleep(0.01)
        assert (await c.get("/api/voice", params={"lang": "ko"})).json()["state"] == "ready"


async def test_unknown_model_is_422(tmp_path):
    app, *_ = build(tmp_path)
    async with http(app) as c:
        assert (await c.post("/api/voice/model", json={"model": "whisper"})).status_code == 422


async def test_remove_returns_to_offered(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    app, voice, *_ = build(tmp_path, downloader=dl, meta={"voice_model": vm.SENSE_ID})
    async with http(app) as c:
        assert (await c.get("/api/voice", params={"lang": "ko"})).json()["state"] == "ready"
        v = (await c.delete("/api/voice/model", params={"lang": "ko"})).json()
        assert v["state"] == "offered"
        assert vm.SENSE_ID not in dl.ready_ids


async def test_transcribe_returns_text_and_passes_the_hint(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    client = FakeClient()
    app, *_ = build(tmp_path, downloader=dl, client=client, meta={"voice_model": vm.SENSE_ID, "ui_lang": "ko", "ui_lang_from": "app"})
    async with http(app) as c:
        r = await c.post("/api/voice/transcribe", content=wav(2.0), headers={"content-type": "audio/wav"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "안녕하세요 Kelo" and body["lang"] == "ko" and body["seconds"] == 2.0
    assert client.calls == [(vm.SENSE_ID, "ko", 2 * 16000 * 2)]


async def test_zh_hant_owner_gets_traditional_text(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    client = FakeClient(text="<|zh|>说话")
    app, *_ = build(tmp_path, downloader=dl, client=client, converter=lambda s: s.replace("说", "說"),
                    meta={"voice_model": vm.SENSE_ID, "ui_lang": "zh-Hant", "ui_lang_from": "app"})
    async with http(app) as c:
        r = await c.post("/api/voice/transcribe", content=wav(1.0))
    assert r.json()["text"] == "說话"
    assert client.calls[0][1] == "zh"


async def test_too_long_and_wrong_format_are_refused(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    app, *_ = build(tmp_path, downloader=dl, meta={"voice_model": vm.SENSE_ID})
    async with http(app) as c:
        assert (await c.post("/api/voice/transcribe", content=wav(125.5))).status_code == 413
        assert (await c.post("/api/voice/transcribe", content=wav(1.0, rate=44100))).status_code == 415
        assert (await c.post("/api/voice/transcribe", content=wav(1.0, channels=2))).status_code == 415
        assert (await c.post("/api/voice/transcribe", content=b"not a wav")).status_code == 415


async def test_oversize_body_is_refused_without_reading_it_all(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    app, *_ = build(tmp_path, downloader=dl, meta={"voice_model": vm.SENSE_ID})
    big = b"\x00" * (voice_mod.MAX_BODY + 100_000)

    async def chunks():
        for i in range(0, len(big), 65536):
            yield big[i:i + 65536]

    async with http(app) as c:
        r = await c.post("/api/voice/transcribe", content=chunks())
    assert r.status_code == 413 and r.json() == {"code": "voice_too_long"}


async def test_not_ready_is_409(tmp_path):
    app, *_ = build(tmp_path, meta={"voice_model": vm.SENSE_ID})
    async with http(app) as c:
        assert (await c.post("/api/voice/transcribe", content=wav(1.0))).status_code == 409


async def test_fourth_request_while_busy_gets_one_429(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    client = FakeClient(delay=0.3)
    app, *_ = build(tmp_path, downloader=dl, client=client, meta={"voice_model": vm.SENSE_ID})
    async with http(app) as c:
        results = await asyncio.gather(*[c.post("/api/voice/transcribe", content=wav(0.5)) for _ in range(4)])
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 200, 200, 429]


async def test_timeout_is_504(tmp_path, monkeypatch):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    monkeypatch.setattr(voice_mod, "TIMEOUT_S", 0.05)
    app, *_ = build(tmp_path, downloader=dl, client=FakeClient(delay=5), meta={"voice_model": vm.SENSE_ID})
    async with http(app) as c:
        r = await c.post("/api/voice/transcribe", content=wav(0.5))
    assert r.status_code == 504 and r.json() == {"code": "voice_timeout"}


async def test_container_down_is_503(tmp_path):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    app, *_ = build(tmp_path, downloader=dl, client=FakeClient(raises=ConnectionError("gone")),
                    meta={"voice_model": vm.SENSE_ID})
    async with http(app) as c:
        assert (await c.post("/api/voice/transcribe", content=wav(0.5))).status_code == 503


async def test_audio_is_not_stored_or_logged(tmp_path, caplog):
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    state = tmp_path / "state"
    state.mkdir()
    before = sorted(os.listdir(tmp_path))
    app, *_ = build(tmp_path, downloader=dl, meta={"voice_model": vm.SENSE_ID})
    caplog.set_level(logging.DEBUG)
    async with http(app) as c:
        for _ in range(5):
            r = await c.post("/api/voice/transcribe", content=wav(1.0))
            assert r.status_code == 200
    assert sorted(os.listdir(tmp_path)) == before
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "안녕하세요" not in text and "seconds=" in text and "took_ms=" in text


def test_the_real_opencc_converter_is_wired_for_zh_hant():
    from assistant import server as server_mod
    conv = server_mod._zh_hant_converter()
    assert conv is not None and conv("简体中文") == "簡體中文"
