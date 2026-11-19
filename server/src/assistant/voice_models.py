"""The speech models the owner can download for server-side voice input, and which one fits a language.

Pure data: no I/O. Every file is pinned to a Hugging Face commit (never /main/) with its size and sha256, so a download
is checked byte for byte. Languages are Kelo locale codes (see i18n.available()).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import i18n

_SENSE_REPO = "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/"
_SENSE_COMMIT = "2365baeacb507f821a0c8120fcee3d484dba7a07"
_PARAKEET_REPO = "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/resolve/"
_PARAKEET_COMMIT = "ed5d745623472f97cfc2e6c2916ee248798fc5a8"

SENSE_ID = "sensevoice-small"
PARAKEET_ID = "parakeet-tdt-v3"


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SpeechModel:
    id: str
    name: str
    maker: str
    license_name: str
    license_url: str
    source_url: str
    attribution: str
    languages: tuple[str, ...]
    kind: str
    files: tuple[ModelFile, ...]
    memory_bytes: int

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def view(self) -> dict[str, Any]:
        """The public face of a model (no file list, no URLs of the files)."""
        return {
            "id": self.id,
            "name": self.name,
            "maker": self.maker,
            "size_bytes": self.size_bytes,
            "memory_bytes": self.memory_bytes,
            "languages": list(self.languages),
            "license_name": self.license_name,
            "license_url": self.license_url,
            "source_url": self.source_url,
            "attribution": self.attribution,
        }


CATALOG: dict[str, SpeechModel] = {
    SENSE_ID: SpeechModel(
        id=SENSE_ID,
        name="SenseVoice Small",
        maker="FunAudioLLM (Tongyi Lab, Alibaba Group)",
        license_name="FunASR Model License",
        license_url="https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE",
        source_url="https://github.com/FunAudioLLM/SenseVoice",
        attribution=(
            "SenseVoice Small (speech recognition) by FunAudioLLM / Tongyi Lab, Alibaba Group. "
            "FunASR Model License. ONNX int8 conversion by k2-fsa, used unchanged."
        ),
        languages=("ko", "ja", "zh-Hans", "zh-Hant"),
        kind="sense_voice",
        files=(
            ModelFile(
                name="model.int8.onnx",
                url=_SENSE_REPO + _SENSE_COMMIT + "/model.int8.onnx",
                size=239_233_841,
                sha256="c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
            ),
            ModelFile(
                name="tokens.txt",
                url=_SENSE_REPO + _SENSE_COMMIT + "/tokens.txt",
                size=315_894,
                sha256="f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
            ),
        ),
        memory_bytes=800 * 1024 * 1024,
    ),
    PARAKEET_ID: SpeechModel(
        id=PARAKEET_ID,
        name="Parakeet TDT 0.6B v3",
        maker="NVIDIA",
        license_name="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        source_url="https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3",
        attribution=(
            "Parakeet TDT 0.6B v3 (speech recognition) by NVIDIA. Creative Commons Attribution 4.0 (CC BY 4.0). "
            "Changed: converted to ONNX and quantized to int8 by k2-fsa, used unchanged by Kelo."
        ),
        languages=("en", "de", "es", "fr", "it", "nl", "pl", "pt-BR", "ru", "uk"),
        kind="nemo_transducer",
        files=(
            ModelFile(
                name="encoder.int8.onnx",
                url=_PARAKEET_REPO + _PARAKEET_COMMIT + "/encoder.int8.onnx",
                size=652_282_294,
                sha256="1afbe69c1ffdabee94755f42e4daf1d93549131a3da11356bda84fcef0f2c479",
            ),
            ModelFile(
                name="decoder.int8.onnx",
                url=_PARAKEET_REPO + _PARAKEET_COMMIT + "/decoder.int8.onnx",
                size=11_845_274,
                sha256="0222a86147299d4dee90edf95b6d13827edb42ad8b7c9cf0b2582f030937c0b5",
            ),
            ModelFile(
                name="joiner.int8.onnx",
                url=_PARAKEET_REPO + _PARAKEET_COMMIT + "/joiner.int8.onnx",
                size=6_355_277,
                sha256="cbd02f3696e9944970111fb439d9c3fd365a80374565f13f2b9c7007d253fc98",
            ),
            ModelFile(
                name="tokens.txt",
                url=_PARAKEET_REPO + _PARAKEET_COMMIT + "/tokens.txt",
                size=93_939,
                sha256="d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d",
            ),
        ),
        memory_bytes=1536 * 1024 * 1024,
    ),
}

# ---- Kelo's voice (text to speech for call mode). Not in CATALOG: CATALOG is the speech-to-text list that recommend() reads.
_SUPERTONIC_REPO = "https://huggingface.co/csukuangfj2/sherpa-onnx-supertonic-3-tts-int8-2026-05-11/resolve/"
_SUPERTONIC_COMMIT = "cca5a0e6c96e1d2c720986bf7e75fcc81dee3ae4"
SUPERTONIC_ID = "supertonic-3"
SPEAK_LANGS: tuple[str, ...] = (
    "en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id",
    "it", "lt", "lv", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi",
)

_SUPERTONIC_FILES = (
    ("duration_predictor.int8.onnx", 3_700_147, "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db"),
    ("text_encoder.int8.onnx", 36_416_150, "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff"),
    ("vector_estimator.int8.onnx", 78_400_833, "20cd86fa5c6effedfda0e7cffe5b0569ca401c440a0c3a1d72bf39286c0db3fd"),
    ("vocoder.int8.onnx", 25_991_073, "e923d60f53f95eb1ce235f1dc33ec56d9c057823c96fa6f8acf98f32b0da6152"),
    ("tts.json", 8_253, "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09"),
    ("unicode_indexer.bin", 262_144, "8402ca48e5189a8950138580b0fff64db6f072f24ac07cd54ba8b2fbb9883b30"),
    ("voice.bin", 517_168, "67d5209b0ee8ce6c74105ffbe12fe6a7628aea3b4ba2fcb308a4a67938a93ce8"),
    ("LICENSE", 1_070, "0dfe0d0ba84416fe3879d9a34f4909d8d0137c78d1e95834177b0414ac096fa2"),
)

SPEECH_OUT: dict[str, SpeechModel] = {
    SUPERTONIC_ID: SpeechModel(
        id=SUPERTONIC_ID,
        name="Supertonic 3",
        maker="Supertone Inc.",
        license_name="BigScience OpenRAIL-M",
        license_url="https://huggingface.co/Supertone/supertonic-3/blob/3cadd1ee6394adea1bd021217a0e650ede09a323/LICENSE",
        source_url="https://huggingface.co/Supertone/supertonic-3",
        attribution=(
            "Supertonic 3 (text to speech) by Supertone Inc. BigScience OpenRAIL-M; the use restrictions in its Attachment A "
            "apply. ONNX int8 conversion by k2-fsa, used unchanged."
        ),
        languages=SPEAK_LANGS,
        kind="supertonic",
        files=tuple(
            ModelFile(name=n, url=_SUPERTONIC_REPO + _SUPERTONIC_COMMIT + "/" + n, size=size, sha256=sha)
            for n, size, sha in _SUPERTONIC_FILES
        ),
        memory_bytes=512 * 1024 * 1024,
    ),
}

ALL: dict[str, SpeechModel] = {**CATALOG, **SPEECH_OUT}

# Kelo's own names for the ten voices: sid 0-4 female, 5-9 male (the order the owner corrected).
VOICES: tuple[tuple[int, str, int], ...] = tuple((sid, "female" if sid < 5 else "male", sid % 5 + 1) for sid in range(10))


def speak_lang(tag: str) -> str | None:
    """The Supertonic language for a BCP-47 tag or Kelo locale: its primary subtag lower-cased ("pt-BR" -> "pt", "en-US" -> "en")
    when SPEAK_LANGS has it, else None (zh-*, th, anything unknown: the device voice speaks it)."""
    primary = (tag or "").replace("_", "-").split("-", 1)[0].lower()
    return primary if primary in SPEAK_LANGS else None


# Locale -> model. Locales not listed (ar, hi, id, th, tr, vi) keep the phone's own recognizer (brief D1).
_FOR_LOCALE: dict[str, str] = {
    "ko": SENSE_ID, "ja": SENSE_ID, "zh-Hans": SENSE_ID, "zh-Hant": SENSE_ID,
    "en": PARAKEET_ID, "de": PARAKEET_ID, "es": PARAKEET_ID, "fr": PARAKEET_ID, "it": PARAKEET_ID,
    "nl": PARAKEET_ID, "pl": PARAKEET_ID, "pt-BR": PARAKEET_ID, "ru": PARAKEET_ID, "uk": PARAKEET_ID,
}


def recommend(language: str) -> SpeechModel | None:
    """The model for the owner's language (tags resolved like i18n.candidates), or None when neither model covers it."""
    for candidate in i18n.candidates(language or ""):
        if candidate in _FOR_LOCALE:
            return CATALOG[_FOR_LOCALE[candidate]]
    return None


# The language hint each model takes. SenseVoice: sherpa-onnx from_sense_voice(language=...) takes auto, zh, en, ja, ko, yue; a
# given language is more accurate than auto. Parakeet TDT v3: sherpa-onnx from_transducer(model_type="nemo_transducer") has no
# language argument, so it only auto-detects (no hint).
_SENSE_HINT = {"ko": "ko", "ja": "ja", "zh-Hans": "zh", "zh-Hant": "zh"}


def model_for(language: str) -> SpeechModel | None:
    """Same as recommend(): the model that serves the owner's language, or None (no prompt, phone recognizer)."""
    return recommend(language)


def language_hint(language: str) -> str | None:
    """The language hint to pass with the model for this locale, or None when the model takes no hint."""
    model = recommend(language)
    if model is None or model.id != SENSE_ID:
        return None
    for candidate in i18n.candidates(language or ""):
        if candidate in _SENSE_HINT:
            return _SENSE_HINT[candidate]
    return None
