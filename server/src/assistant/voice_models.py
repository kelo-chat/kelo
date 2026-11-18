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
