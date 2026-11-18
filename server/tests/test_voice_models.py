"""The speech models Kelo can download for server-side voice input: the catalogue and the language rule (no I/O)."""

import re

import pytest

from assistant import i18n
from assistant import voice_models as vm

SENSE = "sensevoice-small"
PARAKEET = "parakeet-tdt-v3"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_catalog_has_the_two_models_with_pinned_urls_and_sizes():
    assert set(vm.CATALOG) == {SENSE, PARAKEET}
    sense = vm.CATALOG[SENSE]
    parakeet = vm.CATALOG[PARAKEET]
    assert sense.kind == "sense_voice" and parakeet.kind == "nemo_transducer"
    assert {f.name: f.size for f in sense.files} == {
        "model.int8.onnx": 239_233_841,
        "tokens.txt": 315_894,
    }
    assert {f.name: f.size for f in parakeet.files} == {
        "encoder.int8.onnx": 652_282_294,
        "decoder.int8.onnx": 11_845_274,
        "joiner.int8.onnx": 6_355_277,
        "tokens.txt": 93_939,
    }
    assert sense.size_bytes == 239_233_841 + 315_894
    assert parakeet.size_bytes == 652_282_294 + 11_845_274 + 6_355_277 + 93_939


def test_every_file_is_pinned_to_a_commit_never_main():
    for model in vm.CATALOG.values():
        for f in model.files:
            assert "/resolve/" in f.url and "/main/" not in f.url, f.url
            commit = f.url.split("/resolve/", 1)[1].split("/", 1)[0]
            assert HEX40.match(commit), f.url
            assert f.url.startswith("https://huggingface.co/"), f.url
            assert HEX64.match(f.sha256), f.name
            assert f.url.endswith("/" + f.name), f.url


def test_sensevoice_is_the_2024_official_conversion_not_the_cantonese_one():
    urls = [f.url for f in vm.CATALOG[SENSE].files]
    assert all("sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" in u for u in urls)
    assert all("2025-09-09" not in u for u in urls)


def test_licenses_and_attribution_name_model_maker_and_licence():
    sense = vm.CATALOG[SENSE]
    parakeet = vm.CATALOG[PARAKEET]
    assert sense.license_name == "FunASR Model License"
    assert "FunAudioLLM" in sense.attribution and "FunASR Model License" in sense.attribution
    assert parakeet.license_name == "CC BY 4.0"
    assert "NVIDIA" in parakeet.attribution and "CC BY 4.0" in parakeet.attribution
    assert parakeet.license_url == "https://creativecommons.org/licenses/by/4.0/"


@pytest.mark.parametrize(
    "lang, model",
    [
        ("ko", SENSE), ("ja", SENSE), ("zh-Hans", SENSE), ("zh-Hant", SENSE),
        ("en", PARAKEET), ("de", PARAKEET), ("es", PARAKEET), ("fr", PARAKEET), ("it", PARAKEET),
        ("nl", PARAKEET), ("pl", PARAKEET), ("pt-BR", PARAKEET), ("ru", PARAKEET), ("uk", PARAKEET),
    ],
)
def test_recommend_by_owner_language(lang, model):
    assert vm.recommend(lang).id == model


@pytest.mark.parametrize("lang", ["ar", "hi", "id", "th", "tr", "vi"])
def test_languages_without_a_model_get_none(lang):
    assert vm.recommend(lang) is None


def test_every_app_locale_is_decided_and_aliases_go_through_candidates():
    for lang in i18n.available():
        result = vm.recommend(lang)
        assert result is None or result.id in vm.CATALOG
    assert vm.recommend("pt").id == PARAKEET          # -> pt-BR
    assert vm.recommend("zh-TW").id == SENSE          # -> zh-Hant
    assert vm.recommend("zh-HK").id == SENSE
    assert vm.recommend("xx-YY") is None


def test_model_views_hold_only_public_fields():
    view = vm.CATALOG[PARAKEET].view()
    assert view["id"] == PARAKEET and view["name"] == "Parakeet TDT 0.6B v3"
    assert view["size_bytes"] == vm.CATALOG[PARAKEET].size_bytes
    assert view["memory_bytes"] == vm.CATALOG[PARAKEET].memory_bytes
    assert view["license_url"] and view["source_url"] and view["attribution"]
    assert "files" not in view and "/resolve/" not in str(view)


@pytest.mark.parametrize(
    "lang, hint",
    [("ko", "ko"), ("ja", "ja"), ("zh-Hans", "zh"), ("zh-Hant", "zh"), ("zh-TW", "zh"), ("en", None), ("de", None)],
)
def test_language_hint_is_what_the_model_accepts(lang, hint):
    # SenseVoice takes auto, zh, en, ja, ko, yue (sherpa-onnx from_sense_voice). Parakeet TDT v3 takes no hint (auto only).
    assert vm.language_hint(lang) == hint


def test_sensevoice_hint_values_are_valid_sherpa_values():
    for lang in ("ko", "ja", "zh-Hans", "zh-Hant"):
        assert vm.language_hint(lang) in {"auto", "zh", "en", "ja", "ko", "yue"}


@pytest.mark.parametrize("lang", ["ar", "hi", "id", "th", "tr", "vi"])
def test_unsupported_language_has_no_model_and_no_hint(lang):
    assert vm.model_for(lang) is None
    assert vm.language_hint(lang) is None
