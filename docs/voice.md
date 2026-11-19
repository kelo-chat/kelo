# Voice input on the Kelo server

Voice input in the apps can be turned into text by the Kelo server instead of the phone's own recognizer. The owner taps the mic,
records, taps stop, and the audio goes to the server; a local speech model there turns it into text, which goes into the input
field. Nothing is sent by itself.

## What runs where

- The apps record 16 kHz mono PCM and send it as a WAV to `POST /api/voice/transcribe`, inside KE2E like every API call.
- The server (`server/src/assistant/voice.py`, `api/voice.py`) checks the length and format, lets one recognition run at a time
  (at most two more wait; the rest get 429), and hands the raw frames to the `voice` container over `voice.sock` in the `voice-io`
  volume.
- The `voice` container (`ROLE=voice`, `server/src/assistant/voice_service.py`) has no network (`network_mode: none`), no env file,
  a read-only root and no keys. It runs Silero VAD, splits the speech into segments of at most 20 seconds, and decodes one segment at
  a time with sherpa-onnx.
- The model files are downloaded by the server (`voice_download.py`) into the `voice-models` volume, which the server writes and the
  `voice` container reads (read-only).

## Models

| Model | Used for | Files (Hugging Face commit, sizes in `voice_models.py`) |
|---|---|---|
| SenseVoice Small (2024-07-17 int8) | Korean, Japanese, Chinese (Simplified and Traditional) | `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` |
| Parakeet TDT 0.6B v3 (int8) | English, German, Spanish, French, Italian, Dutch, Polish, Portuguese (Brazil), Russian, Ukrainian | `csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` |

- Every file is pinned to a commit (never `main`) and checked by its SHA-256 before it is used.
- Languages neither model covers (Arabic, Hindi, Indonesian, Thai, Turkish, Vietnamese) keep the phone's recognizer; there is no
  prompt for them.
- SenseVoice takes the owner's language as a hint (`ko`, `ja`, `zh`); Parakeet TDT v3 takes no hint in sherpa-onnx and detects the
  language itself.
- zh-Hant owners get Traditional characters: SenseVoice writes Simplified, and the server converts it with OpenCC (`s2twp`).
- Silero VAD (MIT) is baked into the image at `/opt/voice/silero_vad.onnx`, pinned in `docker/voice.lock`.

## Limits and memory

- At most 2 minutes per recording (the bar counts down the last 10 seconds; at 2:00 it stops and sends by itself). The server refuses
  more with 413 `voice_too_long`.
- `VOICE_MEMORY` (default 2g) and `VOICE_CPUS` (default unlimited) cap the container; `assistant limits --voice-memory` sets them.
- The model is loaded when first used and dropped after `VOICE_IDLE_SECONDS` (default 600) without use.
- Disk: about 240 MB (SenseVoice) or 670 MB (Parakeet) per copy, in that copy's `voice-models` volume.
- Measured on an Apple-silicon Mac (sherpa-onnx 2.0.1, 2 threads, the repos' own test clips repeated with 1.5 s pauses):
  SenseVoice 10 s 0.22 s, 60 s 1.1 s, 120 s 2.2 s, peak RAM about 0.7 GB; Parakeet TDT v3 10 s 1.6 s, 60 s 2.8 s, 120 s 5.5 s,
  peak RAM about 1.3 GB. The Linux host numbers are measured by the coordinator; smaller machines will be slower.

## Privacy

- The audio is held in memory for one recognition, then dropped. It is never written to disk, the chat, attachments, the turn log or
  a log line; the server logs only the length in seconds and the time taken.
- Only the model's text is returned to the app. Nothing is sent by the app until the owner taps send.

## Removing the model

My → Voice input → Remove model deletes the model folder on the server and returns the apps to the phone's recognizer. The same is
`DELETE /api/voice/model`. Downloads resume if they were cut off (HTTP Range); a finished file that fails its SHA-256 is deleted.

## API

- `GET /api/voice?lang=` — state (`off`, `offered`, `downloading`, `ready`, `failed`, `unsupported`), recommended and chosen model,
  progress, runtime (`ready` or `missing`).
- `POST /api/voice/model` `{"model": "sensevoice-small" | "parakeet-tdt-v3"}` — choose and download (202 while downloading).
- `POST /api/voice/decline`, `DELETE /api/voice/model`.
- `POST /api/voice/transcribe` — body `audio/wav`, PCM16 mono 16000 Hz, up to 125 s; answers `{"text", "lang", "seconds", "took_ms"}`
  or `{"code": ...}` with 409 `voice_not_ready`, 413 `voice_too_long`, 415 `voice_format`, 429 `voice_busy`,
  503 `voice_runtime_missing`, 504 `voice_timeout`.

## Upgrades

The `voice` service is in the server image (`KELO_IMAGE`), so an update rebuilds it with the server. The compose level is 3: a copy
whose `compose.yml` is older runs the installer again. A copy without the `voice` container answers `runtime: missing`, and the apps
then keep the phone's recognizer.

## Kelo's voice (text to speech)

Call mode (docs/calls.md) speaks Kelo's replies with Supertonic 3, through sherpa-onnx, in the same `voice` container.

- **The model and its files.** Supertonic 3 (`csukuangfj2/sherpa-onnx-supertonic-3-tts-int8-2026-05-11` at commit
  `cca5a0e6c96e1d2c720986bf7e75fcc81dee3ae4`, the original weights `Supertone/supertonic-3`). Eight files, 145,296,838 bytes in all,
  listed with their sizes and SHA-256 in `voice_models.py` (`SPEECH_OUT`). Every file is checked by its SHA-256 before it is used.
- **Where it runs.** The `voice` container loads it from `/voice-models/supertonic-3/` (read-only volume), with its own lock, so a
  transcription and a spoken sentence can run side by side. It is dropped after `VOICE_IDLE_SECONDS`, like the speech-to-text model.
  Its threads are `VOICE_SPEAK_THREADS` (default 2). The container still has no network.
- **Routes** (server, `api/voice.py`, all with auth):
  - `GET /api/voice/speech` — state (`offered`, `off`, `downloading`, `ready`, `failed`), progress, the voice and speed, the ten
    voices (`female` 1–5, `male` 1–5), the languages, runtime.
  - `POST /api/voice/speech/model` (202 while downloading), `POST /api/voice/speech/decline`, `DELETE /api/voice/speech/model`.
  - `PUT /api/voice/speech/prefs` `{"voice"?: 0–9, "speed"?: 0.7–1.5}` — saved on the server, so all three apps sound the same.
  - `POST /api/voice/speak` `{"text", "lang", "voice"?, "speed"?}` — answers `audio/wav` (PCM16 mono at the model's 44,100 Hz) with
    `X-Kelo-Seconds` and `X-Kelo-Took-Ms`. Errors: 409 `voice_speech_not_ready`, 413 `voice_speech_too_long` (over 1000
    characters), 422 `voice_speech_lang` | `voice_speech_args` | `voice_speech_empty`, 429 `voice_busy`, 503
    `voice_runtime_missing`, 504 `voice_timeout`.
- **Languages.** The 31 languages Supertonic 3 speaks (`voice_models.SPEAK_LANGS`). Chinese and Thai are not among them: the apps
  speak those sentences with the device's own voice.
- **Numbers.** Before a sentence is spoken, `spoken_numbers.py` rewrites the shapes the model misreads (clock times, decimal points,
  grouped amounts) into words of the sentence's language. The rules come from `num2words` (LGPL-2.1, unmodified) and Babel's CLDR
  data (BSD-3-Clause); there is no word list in Kelo.
- **Privacy.** The text and the audio are held in memory for one request and never stored or logged. The log line has the length
  in characters and the timings only.
- **Removing it.** `DELETE /api/voice/speech/model` deletes the model folder. Without the model the apps speak with the device's voice.
- **Licence.** Supertonic 3's weights are under the BigScience OpenRAIL-M licence (its LICENSE file is kept in the model folder).
  Its Attachment A use restrictions apply to whoever uses the model, including the medical-advice restriction (Kelo's words come from
  the chat model; the voice only reads them). Kelo does not ship the weights: the server downloads them when the owner agrees. The
  conversion's code is MIT (Supertone Inc.) and is used unchanged.
