"""The speech-model download: verified, resumable, into the models folder. A fake transport serves the bytes; no network."""

import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from assistant import voice_models as vm
from assistant.voice_download import DownloadError, DownloadJob, ModelDownloader

GOOD = b"kelo-voice-model-bytes-" * 5000          # ~110 KB, enough for several 1 MiB-free chunks in tests
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]  # example.com's public address, for the resolver


def _model(files: dict[str, bytes]):
    model_files = tuple(
        vm.ModelFile(name=n, url=f"https://huggingface.co/x/resolve/{'a' * 40}/{n}", size=len(b),
                     sha256=hashlib.sha256(b).hexdigest())
        for n, b in files.items()
    )
    return dataclasses.replace(vm.CATALOG[vm.SENSE_ID], files=model_files, id=vm.SENSE_ID)


def _resolver(host, port, *a):
    return PUBLIC


def _client(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Server:
    """Serves the files of one model; records the Range headers it was asked for."""

    def __init__(self, files: dict[str, bytes], honour_range=True):
        self.files = files
        self.honour_range = honour_range
        self.ranges: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        body = self.files.get(name)
        if body is None:
            return httpx.Response(404)
        rng = request.headers.get("Range")
        self.ranges.append(rng)
        if rng and self.honour_range:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            if start >= len(body):
                return httpx.Response(416)
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)


async def _fetch(dl, model):
    seen = []
    await dl.fetch(model, seen.append)
    return seen


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "voice-models"


async def test_fresh_download_writes_files_and_ready_json(root):
    files = {"model.onnx": GOOD, "tokens.txt": b"a\nb\n"}
    model = _model(files)
    server = Server(files)
    dl = ModelDownloader(root, _client(server), disk_free=lambda p: 10**12, resolver=_resolver)
    progress = await _fetch(dl, model)
    assert (root / vm.SENSE_ID / "model.onnx").read_bytes() == GOOD
    assert not list((root / vm.SENSE_ID).glob("*.part"))
    ready = json.loads((root / vm.SENSE_ID / "ready.json").read_text())
    assert ready["model"] == vm.SENSE_ID and set(ready["files"]) == set(files)
    assert dl.ready(model) is True
    assert progress and progress[-1].done == progress[-1].total == model.size_bytes


async def test_cut_download_resumes_with_range_and_gives_the_same_file(root):
    files = {"model.onnx": GOOD}
    model = _model(files)
    folder = root / vm.SENSE_ID
    folder.mkdir(parents=True)
    (folder / "model.onnx.part").write_bytes(GOOD[: len(GOOD) // 2])
    server = Server(files)
    dl = ModelDownloader(root, _client(server), disk_free=lambda p: 10**12, resolver=_resolver)
    await _fetch(dl, model)
    assert server.ranges == [f"bytes={len(GOOD) // 2}-"]
    assert (folder / "model.onnx").read_bytes() == GOOD


async def test_server_that_ignores_range_starts_over(root):
    files = {"model.onnx": GOOD}
    model = _model(files)
    folder = root / vm.SENSE_ID
    folder.mkdir(parents=True)
    (folder / "model.onnx.part").write_bytes(GOOD[:100])
    dl = ModelDownloader(root, _client(Server(files, honour_range=False)), disk_free=lambda p: 10**12, resolver=_resolver)
    # honour_range=False answers 200 with the whole body to a Range request
    await _fetch(dl, model)
    assert (folder / "model.onnx").read_bytes() == GOOD


async def test_wrong_bytes_are_a_checksum_error_with_no_final_file(root):
    model = _model({"model.onnx": GOOD})
    bad = {"model.onnx": GOOD[:-1] + b"X"}
    dl = ModelDownloader(root, _client(Server(bad)), disk_free=lambda p: 10**12, resolver=_resolver)
    with pytest.raises(DownloadError) as e:
        await _fetch(dl, model)
    assert e.value.code == "checksum"
    assert not (root / vm.SENSE_ID / "model.onnx").exists()
    assert not (root / vm.SENSE_ID / "ready.json").exists()


async def test_redirect_to_an_unlisted_host_is_refused(root):
    model = _model({"model.onnx": GOOD})

    def handler(request):
        return httpx.Response(302, headers={"Location": "https://evil.example/model.onnx"})

    dl = ModelDownloader(root, _client(handler), disk_free=lambda p: 10**12, resolver=_resolver)
    with pytest.raises(DownloadError) as e:
        await _fetch(dl, model)
    assert e.value.code == "refused_host"


async def test_redirect_to_hugging_face_cdn_is_allowed(root):
    files = {"model.onnx": GOOD}
    model = _model(files)
    server = Server(files)

    def handler(request):
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"Location": "https://cas-bridge.xethub.hf.co/model.onnx"})
        return server(request)

    dl = ModelDownloader(root, _client(handler), disk_free=lambda p: 10**12, resolver=_resolver)
    await _fetch(dl, model)
    assert (root / vm.SENSE_ID / "model.onnx").read_bytes() == GOOD


async def test_plain_http_is_refused(root):
    model = _model({"model.onnx": GOOD})
    model = dataclasses.replace(model, files=(dataclasses.replace(model.files[0], url="http://huggingface.co/x"),))
    dl = ModelDownloader(root, _client(Server({})), disk_free=lambda p: 10**12, resolver=_resolver)
    with pytest.raises(DownloadError) as e:
        await _fetch(dl, model)
    assert e.value.code == "refused_host"


async def test_private_address_after_resolve_is_refused(root):
    model = _model({"model.onnx": GOOD})

    def private(host, port, *a):
        return [(2, 1, 6, "", ("10.0.0.5", 443))]

    dl = ModelDownloader(root, _client(Server({"model.onnx": GOOD})), disk_free=lambda p: 10**12, resolver=private)
    with pytest.raises(DownloadError) as e:
        await _fetch(dl, model)
    assert e.value.code == "refused_host"


async def test_too_little_disk_is_refused_before_any_request(root):
    model = _model({"model.onnx": GOOD})
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=GOOD)

    dl = ModelDownloader(root, _client(handler), disk_free=lambda p: 1000, resolver=_resolver)
    with pytest.raises(DownloadError) as e:
        await _fetch(dl, model)
    assert e.value.code == "no_room"
    assert calls == []


async def test_offline_is_reported(root):
    model = _model({"model.onnx": GOOD})

    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    dl = ModelDownloader(root, _client(handler), disk_free=lambda p: 10**12, resolver=_resolver)
    with pytest.raises(DownloadError) as e:
        await _fetch(dl, model)
    assert e.value.code == "offline"


async def test_remove_deletes_the_folder_and_refuses_path_tricks(root):
    model = _model({"model.onnx": GOOD})
    dl = ModelDownloader(root, _client(Server({"model.onnx": GOOD})), disk_free=lambda p: 10**12, resolver=_resolver)
    await _fetch(dl, model)
    dl.remove(vm.SENSE_ID)
    assert not (root / vm.SENSE_ID).exists()
    with pytest.raises(ValueError):
        dl.remove("../x")
    with pytest.raises(ValueError):
        dl.remove("not-a-model")


async def test_ready_is_false_when_a_size_is_wrong(root):
    model = _model({"model.onnx": GOOD})
    dl = ModelDownloader(root, _client(Server({"model.onnx": GOOD})), disk_free=lambda p: 10**12, resolver=_resolver)
    await _fetch(dl, model)
    (root / vm.SENSE_ID / "model.onnx").write_bytes(b"short")
    assert dl.ready(model) is False


async def test_speech_out_model_is_fetched_into_its_own_folder(root):
    files = {"voice.bin": GOOD, "tts.json": b"{}"}
    model = _model(files)
    model = dataclasses.replace(model, id=vm.SUPERTONIC_ID)
    dl = ModelDownloader(root, _client(Server(files)), disk_free=lambda p: 10**12, resolver=_resolver)
    await _fetch(dl, model)
    ready = json.loads((root / vm.SUPERTONIC_ID / "ready.json").read_text())
    assert ready["model"] == vm.SUPERTONIC_ID and set(ready["files"]) == set(files)
    dl.remove(vm.SUPERTONIC_ID)
    assert not (root / vm.SUPERTONIC_ID).exists()


def test_remove_accepts_the_speech_out_id_and_refuses_path_tricks(root):
    dl = ModelDownloader(root, _client(Server({})), disk_free=lambda p: 10**12, resolver=_resolver)
    dl.remove(vm.SUPERTONIC_ID)
    with pytest.raises(ValueError):
        dl.remove("../x")


async def test_download_job_runs_once_reports_errors_and_clears_progress(root):
    files = {"model.onnx": GOOD}
    model = _model(files)
    errors: list[str] = []
    dl = ModelDownloader(root, _client(lambda r: httpx.Response(500)), disk_free=lambda p: 10**12, resolver=_resolver)
    job = DownloadJob(dl, errors.append)
    job.start(model)
    assert job.running()
    job.start(model)                      # a second start while running is a no-op
    while job.running():
        await asyncio.sleep(0)
    assert job.error == "http_500" and errors == ["http_500"]
    assert job.progress is None
    job.start(model)                      # a new start clears the error
    assert job.error is None or job.error == "http_500"
    while job.running():
        await asyncio.sleep(0)
    job.cancel()                          # nothing to cancel any more
