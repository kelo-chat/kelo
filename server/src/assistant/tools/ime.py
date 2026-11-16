"""Keystrokes of input-method scripts: what a person at a physical keyboard presses to write one character, and the text the input
method shows after each press (the "composition"). Used by humanize.type_text so that Korean is typed key by key through the browser's
input-method path (keydown "Process", composition events, then the committed syllable) instead of one `insertText` per syllable, which a page
sees as a paste (no key events, no composition).

Only Hangul (Korean 2-beolsik layout) is here. To add a script (Japanese, Chinese, ...): write a class with `name`, `covers(ch)` and
`keys(ch)` returning the list of `ImeKey` for one character, and append an instance to `SCRIPTS`; humanize needs no other change. Until
then those characters keep being typed with the plain per-character insert. Pure functions, no I/O, no external package.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImeKey:
    jamo: str        # the letter shown on the key (a compatibility jamo)
    code: str        # the physical key ("KeyR")
    shift: bool      # pressed with Shift
    composed: str    # the composition shown after this press (the syllable as far as it is typed)

    @property
    def vk(self) -> int:
        """Windows virtual key code of the physical key (the letter's upper-case code)."""
        return ord(self.code[-1]) if self.code.startswith("Key") else 0


# 2-beolsik: letter -> key. Shifted jamo are listed with their unshifted key.
_KEYS = dict(zip("ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ", [f"Key{c}" for c in "QWERTYUIOPASDFGHJKLZXCVBNM"]))
_SHIFTED = {"ㅃ": "ㅂ", "ㅉ": "ㅈ", "ㄸ": "ㄷ", "ㄲ": "ㄱ", "ㅆ": "ㅅ", "ㅒ": "ㅐ", "ㅖ": "ㅔ"}

_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_FINALS = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"      # index 0: none
_COMPOUND = {"ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ",
             "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ", "ㄼ": "ㄹㅂ", "ㄽ": "ㄹㅅ", "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ",
             "ㅀ": "ㄹㅎ", "ㅄ": "ㅂㅅ"}
_JOIN = {v: k for k, v in _COMPOUND.items()}


def _press(jamo: str) -> tuple[str, str, bool]:
    base = _SHIFTED.get(jamo, jamo)
    return jamo, _KEYS[base], jamo in _SHIFTED


def _parts(jamo: str) -> str:
    """The letters of a jamo as typed: one, or two for a compound vowel / final."""
    return _COMPOUND.get(jamo, jamo)


def _join(parts: str) -> str:
    return _JOIN.get(parts, parts) if len(parts) == 2 else parts


class HangulScript:
    name = "hangul"

    @staticmethod
    def _is_syllable(ch: str) -> bool:
        return "가" <= ch <= "힣"

    @staticmethod
    def _is_jamo(ch: str) -> bool:
        return ch in _KEYS or ch in _SHIFTED or ch in _COMPOUND

    def covers(self, ch: str) -> bool:
        return len(ch) == 1 and (self._is_syllable(ch) or self._is_jamo(ch))

    def keys(self, ch: str) -> list[ImeKey]:
        """The presses for one syllable (or one single jamo) with the composition after each."""
        if not self.covers(ch):
            return []
        if not self._is_syllable(ch):                       # a lone jamo: its letters in turn
            parts = _parts(ch)
            return [ImeKey(*_press(p), composed=_join(parts[: i + 1])) for i, p in enumerate(parts)]
        n = ord(ch) - 0xAC00
        lead, vowel, tail = n // 588, (n % 588) // 28, n % 28
        pre, v, t = _INITIALS[lead], _VOWELS[vowel], _FINALS[tail] if tail else ""
        out = [ImeKey(*_press(pre), composed=pre)]
        vparts, tparts = _parts(v), _parts(t) if t else ""

        def syllable(vp: str, tp: str) -> str:
            vi = _VOWELS.index(_join(vp))
            ti = _FINALS.index(_join(tp)) if tp else 0
            return chr(0xAC00 + (lead * 21 + vi) * 28 + ti)

        for i, p in enumerate(vparts):
            out.append(ImeKey(*_press(p), composed=syllable(vparts[: i + 1], "")))
        for i, p in enumerate(tparts):
            out.append(ImeKey(*_press(p), composed=syllable(vparts, tparts[: i + 1])))
        return out


SCRIPTS: list[HangulScript] = [HangulScript()]


def script_for(ch: str):
    return next((s for s in SCRIPTS if s.covers(ch)), None)


def uses_ime(text: str) -> bool:
    return any(script_for(ch) is not None for ch in text)
