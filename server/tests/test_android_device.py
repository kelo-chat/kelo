"""The bridge's client (android/device.py): the API as typed calls, errors as DeviceError, off without a long enough token."""

import functools
import json

import httpx
import pytest

from assistant.android.device import DeviceClient, DeviceError

TOKEN = "t" * 40


def client(handler, token=TOKEN, url="http://android:8090"):
    return DeviceClient(url, token, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))


def test_it_is_off_without_a_url_or_a_token_of_32_characters():
    assert client(None).enabled
    assert not client(None, token="short").enabled and not client(None, token="x" * 31).enabled and client(None, token="x" * 32).enabled
    assert not client(None, url="").enabled and not DeviceClient("", "").enabled


async def test_a_disabled_client_never_calls_out():
    calls = []
    c = client(lambda r: calls.append(r) or httpx.Response(200, json={}), token="short")
    with pytest.raises(DeviceError, match="not set up"):
        await c.status()
    assert calls == []


async def test_calls_carry_the_token_and_their_arguments():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.url.params), request.headers.get("authorization"),
                     json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"ok": True, "items": []})
    c = client(handler)
    await c.status()
    await c.screen(image=True)
    await c.screen()
    await c.tap(10, 20)
    await c.swipe(1, 2, 3, 4, 250)
    await c.text("안녕 hi")
    await c.key("back")
    await c.open("com.android.settings")
    await c.apps()
    assert [(m, p) for m, p, *_ in seen] == [("GET", "/status"), ("GET", "/screen"), ("GET", "/screen"), ("POST", "/tap"), ("POST", "/swipe"),
                                              ("POST", "/text"), ("POST", "/key"), ("POST", "/open"), ("GET", "/apps")]
    assert all(h == f"Bearer {TOKEN}" for *_, h, _ in seen)
    assert seen[1][2] == {"image": "1"} and seen[2][2] == {"image": "0"}
    assert seen[3][4] == {"x": 10, "y": 20} and seen[4][4] == {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "ms": 250}
    assert seen[5][4] == {"text": "안녕 hi"} and seen[6][4] == {"key": "back"} and seen[7][4] == {"package": "com.android.settings"}


async def test_an_error_answer_http_errors_and_timeouts_are_device_errors():
    with pytest.raises(DeviceError, match="non-ASCII typing needs the clipboard"):
        await client(lambda r: httpx.Response(502, json={"error": "non-ASCII typing needs the clipboard"})).text("한")
    with pytest.raises(DeviceError, match="HTTP 500"):
        await client(lambda r: httpx.Response(500, text="oops")).status()

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(DeviceError, match="in time"):
        await client(slow).screen()

    def down(request):
        raise httpx.ConnectError("no route", request=request)
    with pytest.raises(DeviceError, match="could not be reached") as e:
        await client(down).status()
    assert TOKEN not in str(e.value)
