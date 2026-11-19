# Calls with Kelo in the app

A call is a spoken conversation with Kelo inside the Kelo app, with the app open. It is not a phone call: there is no CallKit,
no ConnectionService, no VoIP push and no full-screen notification.

## The flow

1. The owner asks for a call in words (in any language, such as "let's talk by phone" in the app's own words), or taps the handset in the chat header. The model may call the tool
   `start_voice_call` (`tools/call_tools.py`): the reply sends the app a `call_offer` event, and the app shows its incoming-call
   screen. A tap on the header shows the same screen.
2. Accept starts the call. The app asks the server to start Kelo's greeting with `POST /api/calls/{call_id}/answered` (optional
   `topic`, up to 120 characters). That starts a turn whose message is hidden: `calls.answered_text` starts with `CALL_MARK`, and
   `GET /api/messages` and search leave it out.
3. The owner speaks. The app detects the end of the speech, sends the clip to the server's speech-to-text model (or, when that is not
   ready, uses the phone's own recognizer), and posts the text to `POST /api/turns` with `call` set to the call id.
4. The server stores the owner's words and Kelo's reply as normal messages with `via = "call"`, so the chat shows them with the call
   mark. For that turn only, the model also gets `calls.CALL_NOTE`: the words came from speech, so some may be misheard; the reply is
   spoken as it is written, so it should be short, spoken sentences, with numbers written the way they are said, and no lists,
   tables, links, code or Markdown.
5. Kelo's reply streams back as text, and the app speaks it sentence by sentence (`ReadAloud`/`SpeechQueue`): with Kelo's own voice
   when the voice model is on the server (`POST /api/voice/speak`), otherwise with the device's voice.
6. Hanging up ends the call. A reply still running keeps going: its text lands in the chat, it is only not spoken.

## Rules

- `can_call` (on `POST /api/turns`) says that the app can show a call screen. Without it, or for an unattended turn, the tool answers
  with an error and nothing rings. Older apps never send it.
- Silence for 5 minutes ends the call, so the microphone is not left open.
- The call ends by itself when the app leaves the foreground or a phone call or alarm takes the sound (the app says so in the chat).
- Approvals and other cards during a call show on the screen; Kelo says in a few words that they are there.

## Platform limits

- Android and iPhone: barge-in is on (the owner may talk over Kelo, and playback stops), using the platform's echo cancellation.
- Desktop: no barge-in by voice (the microphone is ignored while Kelo talks); Space or a tap on Kelo's picture stops the voice. Desktop
  has no phone recognizer, so a call needs the server's speech-to-text model.
- iPhone and Android: while the server's speech-to-text model is not ready, the phone's recognizer hears the owner for that call.

## Where the code is

- Server: `calls.py` (texts), `api/calls.py` (the answered route), `tools/call_tools.py` (the tool), `api/chat.py` and `turn_engine.py`
  (`can_call`), `server.py` `prepare_talk` (`via` and the note), `chat_search.py` (the hidden mark).
- Apps: `apps/shared/.../chat/kelo/app/call/` (the state machine, the sentence cutter, the speech gate, the session), with the Swift
  mirror in `KeloUI/Sources/KeloShared/Voice/CallLogic.swift`.
