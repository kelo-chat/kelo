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


# ---- Kelo's voice (text to speech): the speech-out catalogue, kept apart from the speech-to-text CATALOG

SUPER = "supertonic-3"


def test_speech_model_is_pinned_and_sized_as_checked():
    model = vm.SPEECH_OUT[SUPER]
    assert model.kind == "supertonic" and model.license_name == "BigScience OpenRAIL-M"
    assert model.size_bytes == 145_296_838
    assert {f.name: f.size for f in model.files} == {
        "duration_predictor.int8.onnx": 3_700_147,
        "text_encoder.int8.onnx": 36_416_150,
        "vector_estimator.int8.onnx": 78_400_833,
        "vocoder.int8.onnx": 25_991_073,
        "tts.json": 8_253,
        "unicode_indexer.bin": 262_144,
        "voice.bin": 517_168,
        "LICENSE": 1_070,
    }
    for f in model.files:
        assert "/resolve/cca5a0e6c96e1d2c720986bf7e75fcc81dee3ae4/" in f.url, f.url
        assert "/main/" not in f.url and f.url.endswith("/" + f.name)
        assert HEX64.match(f.sha256), f.name
    assert "3cadd1ee6394adea1bd021217a0e650ede09a323" in model.license_url


def test_stt_catalog_unchanged_and_all_has_both_lists():
    assert set(vm.CATALOG) == {SENSE, PARAKEET}
    assert set(vm.ALL) == {SENSE, PARAKEET, SUPER}


@pytest.mark.parametrize(
    "tag, lang",
    [("en-US", "en"), ("pt-BR", "pt"), ("ko", "ko"), ("ko-KR", "ko"), ("zh-Hans", None), ("zh-Hant", None),
     ("th", None), ("xx", None), ("", None)],
)
def test_speak_lang(tag, lang):
    assert vm.speak_lang(tag) == lang


def test_voices_order_female_then_male():
    assert len(vm.VOICES) == 10
    assert [v for v in vm.VOICES if v[1] == "female"] == [(i, "female", i + 1) for i in range(5)]
    assert [v for v in vm.VOICES if v[1] == "male"] == [(i, "male", i - 4) for i in range(5, 10)]


def test_speak_langs_are_the_thirty_one_checked_languages():
    assert len(vm.SPEAK_LANGS) == 31 and "zh" not in vm.SPEAK_LANGS and "th" not in vm.SPEAK_LANGS
