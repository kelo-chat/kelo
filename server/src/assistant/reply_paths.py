"""No sandbox paths in a reply's text.

The model sees its working folder as a path of its provider's sandbox (agy: a `hermes_agy_*` temp folder; Claude direct: a
`claude-directsdk-*` one). A reply that names such a path (a `file://` link or a bare path) points the user at a folder that is
not theirs. When Kelo's home maps the path to one of Kelo's own files (home.py `relativize`), the reply names that file by its
relative path in backticks instead: `notes/lotto.md`. The apps have no link to Kelo's files (a non-web link is shown as plain
text), so it is text, not a link. Any other path, and any ordinary URL, is left as it is.

Streaming: a path can be split across text deltas, so the text from where a possible path starts is held back until the path is
complete (whitespace or ")" after it), or until the reply ends (flush)."""

from __future__ import annotations

import re
from collections.abc import Callable

SANDBOX_MARKS = ("hermes_agy_", "claude-directsdk-")   # the temp folders the providers run the model in
MAX_HOLD = 500          # a possible path held back longer than this is passed on as it is
TRAILING = ".,;:!?"     # punctuation after a bare path is not part of it
BOUNDARY = " \t\r\n([`<\"'"   # a path can start after these (or at the start of the reply)

# what can still become a markdown link to a sandbox path, or a bare path, at the end of what has arrived so far
PREFIX_LINK = re.compile(r"\[[^\]\n]*(?:\](?:\((?:file://)?[^)\s]*)?)?$")
PREFIX_BARE = re.compile(r"(?<![^\s(\[`<\"'])(?:file://)?/[^\s)\]`<>\"']*$")
PREFIX_SCHEME = re.compile(r"(?<![^\s(\[`<\"'])(?:f|fi|fil|file|file:|file:/|file://)$")
# the complete forms
LINK = re.compile(r"\[([^\]\n]*)\]\(((?:file://)?/[^)\s]*)\)")
BARE = re.compile(r"(?<![^\s(\[`<\"'])((?:file://)?/[^\s)\]`<>\"']+)")


class ReplyPaths:
    """Rewrites the sandbox paths of one reply's text as it streams. `relativize`: home.relativize (an absolute path -> Kelo's
    relative path of that file, or the path itself). None: nothing is rewritten."""

    def __init__(self, relativize: Callable[[str], str] | None):
        self.relativize = relativize
        self.held = ""
        self.at_boundary = True   # the text passed on so far ends where a path may start (a delta may begin inside a word)

    def feed(self, text: str) -> str:
        """The part of the reply that can be passed on now; the rest waits for the next delta (or flush)."""
        if self.relativize is None:
            return text
        data, self.held = self.held + text, ""
        pad = "" if self.at_boundary else "x"   # "x": no path starts right here, whatever the delta before said
        cut = max(0, self._hold_from(pad + data) - len(pad))
        self.held = data[cut:]
        out = self._rewrite_padded(pad, data[:cut])
        if out:
            self.at_boundary = out[-1] in BOUNDARY
        return out

    def flush(self) -> str:
        """The held part, at the end of the reply (or before anything else is passed on)."""
        out, self.held = self.held, ""
        if self.relativize is None:
            return out
        out = self.rewrite(out)
        if out:
            self.at_boundary = out[-1] in BOUNDARY
        return out

    @staticmethod
    def _hold_from(data: str) -> int:
        starts = [m.start() for m in (PREFIX_LINK.search(data), PREFIX_BARE.search(data), PREFIX_SCHEME.search(data)) if m]
        if not starts:
            return len(data)
        start = min(starts)
        return start if len(data) - start <= MAX_HOLD else len(data)

    def _rewrite_padded(self, pad: str, text: str) -> str:
        return self.rewrite(pad + text)[len(pad):] if pad else self.rewrite(text)

    def rewrite(self, text: str) -> str:
        if not text or self.relativize is None:
            return text
        text = LINK.sub(self._link, text)
        return BARE.sub(self._bare, text)

    def _resolve(self, path: str) -> tuple[str, str] | None:
        """(Kelo's relative path, the punctuation that follows it) for a sandbox path that maps to a file of Kelo's, else None."""
        if not any(mark in path for mark in SANDBOX_MARKS):
            return None
        core = path.rstrip(TRAILING)
        rel = self.relativize(core)
        if not rel or rel == core or rel.startswith("/"):
            return None
        return rel, path[len(core):]

    def _link(self, m: re.Match[str]) -> str:
        raw = m.group(2)
        path = raw[len("file://"):] if raw.startswith("file://") else raw
        found = self._resolve(path)
        if found is None:
            return m.group(0)
        rel, tail = found
        return f"`{rel}`{tail}"

    def _bare(self, m: re.Match[str]) -> str:
        raw = m.group(1)
        path = raw[len("file://"):] if raw.startswith("file://") else raw
        found = self._resolve(path)
        if found is None:
            return m.group(0)
        rel, tail = found
        inside_code = m.start() > 0 and m.string[m.start() - 1] == "`"   # already in a code span: the name alone
        return f"{rel}{tail}" if inside_code else f"`{rel}`{tail}"
