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
