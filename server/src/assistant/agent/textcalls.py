"""Tool calls a model wrote into its reply text instead of making them.

CLI-driven models (agy/Gemini above all) are asked to write tool calls as `<tool_call>{json}</tool_call>` text, and any model can slip
into writing one (`<function_call>`, `<tool_use>`, `<function=name>{json}</function>`, a ```json block shaped like a call). When the
provider's own parser misses one (seen on a phone: a `<tool_call>` block whose JSON ended in `" function}}`), the raw markup reached the
chat as text. `TextCallFilter` sits on every provider's text stream (agent/loop.py): it holds back text that may be the start of such a
block until it knows, passes everything else on as it comes, and collects the blocks. `read_block` turns a block into calls of known
tools; light damage (stray words before a closing brace, junk after the object, trailing commas, missing closing braces) is mended,
but arguments are never made up. `strip_text_calls` removes such blocks from stored text, so markup is never shown again or replayed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_TAGS = ("tool_call", "function_call", "tool_use", "toolcall", "functioncall", "tool_calls", "function_calls")
# an opening tag: <tool_call>, <tool_call id="1">, <function=name>
_OPEN = re.compile(r"<(?:(" + "|".join(_TAGS) + r")\b[^<>]*|function=([\w.-]+)\s*)>", re.IGNORECASE)
_FENCE = re.compile(r"```[ \t]*([\w+.-]*)[ \t]*\n")
_CALL_FENCES = {"json", "tool_call", "tool_code", "function_call", "tool", ""}   # other languages are ordinary code
# what a held tail may be the start of: "<", "<tool_c", "<function=na", "`", "``", "```", "```js"
_STARTS = tuple(f"<{t}" for t in _TAGS) + ("<function=",)


def _maybe_start(tail: str) -> bool:
    """`tail` (text after the last '<' or '`' run) could still grow into an opening tag or fence."""
    low = tail.lower()
    if low.startswith("<"):
        if ">" in low or "\n" in low or len(low) > 48:
            return False
        return any(s.startswith(low) or low.startswith(s) for s in _STARTS)
    if low.startswith("`"):
        if "\n" in low:
            return False
        if not low.startswith("```"):
            return set(low) <= {"`"}
        word = low[3:].strip()
        return len(low) <= 20 and any(k.startswith(word) for k in ("json", "tool_call", "tool_code", "function_call", "tool", ""))
    return False


# where a block was taken out of the shown text (TextCallFilter.feed_parts); never part of what is shown
_CUT = "\x00"


@dataclass
class TextCallFilter:
    """Feed it the text deltas of one model reply; it returns what may be shown now and keeps the tool-call blocks in `blocks`."""
    blocks: list[str] = field(default_factory=list)
    _buf: str = ""
    _end: str = ""        # the closing marker of the block being collected ("" = not in a block)
    _skip: int = 0        # where to look for it (after the opening marker)
    _fence: bool = False  # the block is a ``` fence: shown after all when it turns out not to be a call
    _code: bool = False   # inside an ordinary code block that is being shown: passed on up to its closing ```

    def feed(self, text: str) -> str:
        return "".join(self.feed_parts(text))

    def finish(self) -> str:
        return "".join(self.finish_parts())

    def feed_parts(self, text: str) -> list[str]:
        """What may be shown now, split where a tool-call block was taken out: the text after a block was written apart from the text
        before it (a call between them), so a reader joins them as separate paragraphs. One part when nothing was taken out."""
        self._buf += text.replace(_CUT, "")
        return self._drain(final=False).split(_CUT)

    def finish_parts(self) -> list[str]:
        return self._drain(final=True).split(_CUT)

    def _close(self) -> None:
        self._end, self._skip, self._fence = "", 0, False

    def _drain(self, final: bool) -> str:
        out: list[str] = []
        while self._buf:
            if self._code:
                k = self._buf.find("```")
                if k < 0:
                    keep = 0 if final else len(self._buf) - len(self._buf.rstrip("`"))
                    out.append(self._buf[:len(self._buf) - keep])
                    self._buf = self._buf[len(self._buf) - keep:]
                    break
                out.append(self._buf[:k + 3])
                self._buf, self._code = self._buf[k + 3:], False
                continue
            if self._end:
                i = self._buf.lower().find(self._end, self._skip)
                if i < 0:
                    body = self._buf[self._skip:]
                    if self._fence and body.strip() and not body.lstrip().startswith(("{", "[")):
                        out.append(self._buf)   # an ordinary code block: shown as it comes
                        self._buf, self._code = "", True
                        self._close()
                        continue
                    if not final:
                        break
                    raw, self._buf, closed = self._buf, "", False   # never closed: models often leave the closing tag out
                else:
                    cut = i + len(self._end)
                    raw, self._buf, closed = self._buf[:cut], self._buf[cut:], True
                if self._fence and not looks_like_call(raw):
                    out.append(raw)
                elif not self._fence and not closed and "{" not in raw:
                    out.append(raw)   # "<tool_call>" mentioned in prose with nothing after it that could be a call
                else:
                    self.blocks.append(raw)
                    out.append(_CUT)
                self._close()
                continue
            found = [x for x in (_OPEN.search(self._buf), _FENCE.search(self._buf)) if x]
            if not found:
                cut = len(self._buf)
                if not final:
                    for j in (self._buf.rfind("<"), self._buf.rfind("`")):
                        while j > 0 and self._buf[j - 1] == "`" and self._buf[j] == "`":
                            j -= 1
                        if j >= 0 and _maybe_start(self._buf[j:]):
                            cut = min(cut, j)
                out.append(self._buf[:cut])
                self._buf = self._buf[cut:]
                break
            m = min(found, key=lambda x: x.start())
            out.append(self._buf[:m.start()])
            self._buf = self._buf[m.start():]
            self._skip = m.end() - m.start()
            if m.re is _FENCE and m.group(1).lower() not in _CALL_FENCES:
                out.append(self._buf[:self._skip])
                self._buf, self._code, self._skip = self._buf[self._skip:], True, 0
            elif m.re is _FENCE:
                self._end, self._fence = "```", True
            else:
                self._end = f"</{(m.group(1) or 'function').lower()}>"
        return "".join(out)


# ---------------------------------------------------------------- reading a block

def _mend(text: str) -> list[str]:
    """`text` and lightly mended versions of it, most faithful first."""
    out = [text]
    s = re.sub(r'(["\d}\]el])\s+[A-Za-z_][\w-]*\s*(?=[}\]])', r"\1", text)   # stray words before a closing brace: ..." function}}
    s = re.sub(r",\s*(?=[}\]])", "", s)                                          # trailing commas
    out.append(s)
    depth, quoted, esc = [], False, False
    for ch in s:
        if quoted:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                quoted = False
            continue
        if ch == '"':
            quoted = True
        elif ch in "{[":
            depth.append("}" if ch == "{" else "]")
        elif ch in "}]" and depth:
            depth.pop()
    if depth and not quoted:
        out.append(s + "".join(reversed(depth)))   # missing closing braces at the end
    return out


def _loads(text: str) -> Any:
    """The first JSON value in `text` (junk after it is ignored), lightly mended; raises ValueError when there is none."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON")
    dec = json.JSONDecoder()
    for cand in _mend(text[start:]):
        try:
            return dec.raw_decode(cand)[0]
        except ValueError:
            continue
    raise ValueError("unreadable JSON")


def _body(raw: str) -> tuple[str, str | None]:
    """(the inside of a block, the function name its tag gives)."""
    m = re.match(r"\s*<function=([\w.-]+)\s*>", raw, re.IGNORECASE)
    if m:
        return re.sub(r"</function>\s*$", "", raw[m.end():], flags=re.IGNORECASE), m.group(1)
    m = re.match(r"\s*```[^\n]*\n", raw)
    if m:
        return re.sub(r"```\s*$", "", raw[m.end():]), None
    raw = re.sub(r"^\s*<[^<>]*>", "", raw)
    return re.sub(r"</[\w]+>\s*$", "", raw), None


def _call_of(obj: Any, named: str | None) -> tuple[str, Any] | None:
    """(name, arguments) from the shapes models use, or None."""
    if not isinstance(obj, dict):
        return None
    if named:
        return named, obj
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else None
    src = fn or obj
    name = src.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    for key in ("arguments", "parameters", "args", "input"):
        if key in src:
            return name.strip(), src[key]
        if key in obj:
            return name.strip(), obj[key]
    return name.strip(), {}


def _arguments(value: Any) -> str | None:
    """Arguments as a JSON object string, or None when they cannot be read (never guessed)."""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        if not value.strip():
            return "{}"
        try:
            obj = _loads(value)
        except ValueError:
            return None
        return json.dumps(obj, ensure_ascii=False) if isinstance(obj, dict) else None
    return None


def looks_like_call(raw: str) -> bool:
    """A ``` block is taken for a tool call only when its JSON has a call's shape; any other code block is shown as it is."""
    body, named = _body(raw)
    try:
        obj = _loads(body)
    except ValueError:
        return False
    items = obj if isinstance(obj, list) else [obj]
    return bool(items) and all(isinstance(o, dict) and ("function" in o or ("name" in o and ({"arguments", "parameters"} & set(o)))
                                                        or "tool" in o or "tool_name" in o) for o in items)


@dataclass
class BlockReading:
    calls: list[tuple[str | None, str, str]] = field(default_factory=list)   # (id the model gave, name, arguments JSON)
    unreadable: int = 0      # blocks (or calls in them) that could not be read
    unknown: list[str] = field(default_factory=list)                        # names that are not tools


def read_blocks(blocks: list[str], known: set[str] | frozenset[str]) -> BlockReading:
    out = BlockReading()
    for raw in blocks:
        body, named = _body(raw)
        try:
            obj = _loads(body)
        except ValueError:
            out.unreadable += 1
            continue
        for item in obj if isinstance(obj, list) else [obj]:
            got = _call_of(item, named)
            if got is None:
                out.unreadable += 1
                continue
            name, value = got
            args = _arguments(value)
            if args is None:
                out.unreadable += 1
            elif name not in known:
                out.unknown.append(name)
            else:
                ident = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) and not named else None
                out.calls.append((ident, name, args))
    return out


def strip_text_calls(text: str) -> str:
    """`text` without tool-call markup (for stored replies: never shown again, never sent back to a model)."""
    if not text or ("<" not in text and "```" not in text):
        return text
    f = TextCallFilter()
    kept = f.feed(text) + f.finish()
    return re.sub(r"\n{3,}", "\n\n", kept).strip() if f.blocks else text
