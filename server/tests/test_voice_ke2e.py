"""Voice input through KE2E: the recording travels sealed, the text comes back sealed, and the path between sees neither."""

import base64
import json

import httpx

from assistant import e2e
from assistant import voice_models as vm
from assistant.server import Settings, create_app

from e2e_client import E2EClient
from test_e2e_wire import Recorder, call
from test_server import AUTH, TOKEN, Running
from test_voice_api import FakeClient, FakeDownloader, wav

SPOKEN = "오늘 저녁 메뉴는 김치찌개로 하자"


def app_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "s.db"),
                               home_dir=str(home)), tools=[])


async def test_recording_goes_sealed_and_text_comes_back_sealed(tmp_path):
    app = app_for(tmp_path)
    dl = FakeDownloader()
    dl.ready_ids.add(vm.SENSE_ID)
    app.state.voice.downloader = dl
    app.state.voice.client = FakeClient(text=f"<|ko|><|NEUTRAL|>{SPOKEN}")
    app.state.voice.store.set_meta("voice_model", vm.SENSE_ID)
    audio = wav(3.0)
    async with Running(app) as srv, Recorder(srv.port) as proxy, httpx.AsyncClient(base_url=proxy.url, timeout=30) as http:
        info = (await http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        head, body, _ = await call(http, client, "POST", "/api/voice/transcribe",
                                   {"authorization": f"Bearer {TOKEN}", "content-type": "audio/wav"}, audio)
        assert head["s"] == 200, body
        out = json.loads(body)
        assert out["text"] == SPOKEN and out["lang"] == "ko"
        plain = (await httpx.AsyncClient(base_url=srv.url).get("/api/voice", headers=AUTH)).json()
        assert plain["state"] == "ready"
    assert not proxy.holds(SPOKEN) and not proxy.holds(audio[100:200]) and not proxy.holds(b"/api/voice/transcribe\"")
    assert proxy.holds(b"POST /e2e")
