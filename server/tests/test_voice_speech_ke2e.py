"""Kelo's voice through KE2E: POST /api/voice/speak travels sealed; the WAV comes back sealed; the path between sees neither the
text nor the audio (the same rig as test_voice_ke2e.py)."""

import base64
import json

import httpx

from assistant import voice_models as vm
from assistant.server import Settings, create_app

from e2e_client import E2EClient
from test_e2e_wire import Recorder, call
from test_server import AUTH, TOKEN, Running
from test_voice_speech_api import FakeSpeechClient, FakeSpeechDownloader

SPOKEN = "오늘 저녁 메뉴는 김치찌개로 하자"


def app_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "s.db"),
                               home_dir=str(home)), tools=[])


async def test_speech_goes_sealed_and_the_wav_comes_back_sealed(tmp_path):
    app = app_for(tmp_path)
    dl = FakeSpeechDownloader()
    dl.ready_ids.add(vm.SUPERTONIC_ID)
    app.state.voice_out.downloader = dl
    app.state.voice_out.client = FakeSpeechClient(pcm=b"\x10\x00" * 22050)
    app.state.voice_out.store.set_meta("speech_model", vm.SUPERTONIC_ID)
    async with Running(app) as srv, Recorder(srv.port) as proxy, httpx.AsyncClient(base_url=proxy.url, timeout=30) as http:
        info = (await http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        head, body, _ = await call(http, client, "POST", "/api/voice/speak",
                                   {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"},
                                   json.dumps({"text": SPOKEN, "lang": "ko"}).encode())
        assert head["s"] == 200, body
        assert body[:4] == b"RIFF" and body[8:12] == b"WAVE" and len(body) > 44
    assert not proxy.holds(SPOKEN.encode()) and not proxy.holds(body[44:200]) and not proxy.holds(b"/api/voice/speak\"")
    assert proxy.holds(b"POST /e2e")
