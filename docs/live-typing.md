# Live typing in "take control" (live protocol 3)

While the owner holds a browser tab, every keystroke and every step of an input-method composition shows in the page at once:
a Korean initial consonant, then the open syllable, then the closed syllable (U+3131, U+AD6C, U+AD70) forms in the page's own field, as it does on a phone's own browser. Before this, text was typed into a line of
the app and sent as a whole on Send.

## How it works
- The app keeps a hidden one-line **capture field** (Android and desktop: `agent/RemoteKeyboard.kt`; iPhone: `KeloWork/RemoteKeyboard.swift`).
  It always starts with one zero-width space (the "mark"), so Backspace on an empty-looking field still changes it and reaches the page.
- Every change of that field (the composition is part of its text) is turned into **steps** by one pure function (`RemoteTyping.steps` in
  Kotlin and Swift; the same vectors, `apps/shared/test-vectors/remote-typing.json`, are read by both): the common prefix of the old and new
  committed text is kept, the rest becomes `back` (one per grapheme) and `text`, Enter and Tab become keys, and the composing tail becomes
  `compose`. A keystroke goes out at once; changes inside 30 ms are merged into one trailing event; any other input flushes first, so the
  socket keeps the owner's order.
- The server (`tools/live.py`, input type `edit`, `LIVE_V` 3) runs the steps in order as DevTools input: `compose` = `Input.imeSetComposition`
  (the page gets real composition events), `text` = `Input.insertText` (commits a composition), `back` = Backspace key events, `key` = a named key.
  At most 64 steps per event; the whole event is checked before any step runs; typed text is never logged and never in a reply.
- A composition still showing when the hold ends (hand back, timeout, the holder disconnecting) is committed: what the owner saw stays.
- `KELO_LIVE_COMPOSE=0` makes the server type a composition as plain text (delete the previous composition with Backspace, insert the new
  one) for a site that misbehaves with a real composition (`Input.imeSetComposition` is marked experimental in the DevTools protocol).

## Focus
The meta line and the frame header carry `editing`, `kind` (secret, number, tel, email, url, search, multiline, text: what keyboard fits) and
`field` (an opaque 8-hex id that changes when the focus moves to another field; never the value). The look also follows a same-origin frame, and
counts a focused cross-origin frame as a text field. A pointer release or a tap makes the server look again 60 ms and 250 ms later and wake the
meta line at once; the 0.5 s poll stays as the fallback.

## Old servers and old apps
An app uses live typing only when the server says `live_v` 3 or more; otherwise the visible type line stays unchanged. An old app ignores the
new fields. The phone-device take-over (`android/live.py`) still says `live_v` 2: its typing is the device's own.

## Not here
`replacementStart/End` (IME edits far from the end of the text) and live typing for the phone-device take-over.
