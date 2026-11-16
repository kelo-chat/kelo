# Contributing to Kelo

Thanks for helping. Kelo is a self-hosted AI agent: a Python server (`server/src/assistant`), native apps for Android (Kotlin +
Jetpack Compose, the reference app), desktop (Compose Multiplatform) and iOS (SwiftUI), a small web app (`server/web/`) and the
website. Issues are welcome in English or Korean.

## Before you start

- **Bugs and ideas:** open an issue. Steps to reproduce, the app and server versions, and screenshots (with no personal
  data) help most.
- **Security problems:** do not open a public issue. See [SECURITY.md](SECURITY.md).

## Pull requests are not accepted yet

For now Kelo is written by one person, and outside pull requests are closed without review. This keeps the
copyright of the code in one place, which matters because Kelo may also be offered under a commercial licence. When
that changes, this file will say how to contribute. Contributions are under AGPL-3.0, the same licence as the code.

You are free to fork Kelo under the AGPL. The rules below are the ones the project's own code follows.

## Ground rules

1. **Never commit secrets or personal data.** No API keys, tokens, passwords, private keys, keystores, `.env` files,
   `instances/` folders, Play or Firebase service-account files (the app's own `apps/android/kelo/app/google-services.json` is the one
   exception: it is the public client config that is inside every APK, and it is part of the source so that builds match), or real chats, contacts, mail,
   phone numbers or card numbers. Test data must be obviously fake (`example.com` addresses, `4242…` test cards, values
   built from pieces at run time when a scanner would otherwise flag them). If you committed a secret by mistake, tell us
   at support@kelo.chat and revoke it at its provider; deleting the commit is not enough.
2. **Only code you may share.** Write it yourself, or bring in code under a licence compatible with AGPL-3.0 and name its
   source, licence and copyright in the pull request and in `NOTICE`. Never paste code, prompts or assets taken from a
   closed product or a vendor's client.
3. **Same features on every app.** Android is the reference. A user-visible change to one app should come with the same
   change on the others, or an issue that tracks the gap.
4. **Text in every language.** UI text lives in the Android string resources and is shared with desktop and iOS. Add the
   English string, then the translations; layouts must wrap cleanly at narrow widths (check 320 and 412 dp) and with long
   or space-less languages (German, Russian, Thai, Arabic).
5. **Changelog.** Add a line to `CHANGELOG.md` for every user-visible change.
6. **Screenshots** in issues, pull requests and the repository use the mock server or staged data, never a real
   account.

## Setting up and running the tests

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). Docker is needed only to run the full stack.

### Server (Python)

```sh
uv sync                     # create .venv with the server and its test tools
uv run pytest               # all server tests
uv run pytest server/tests/test_vault.py -q    # one file
uvx ruff check server/src server/tests    # lint
```

### Android

```sh
cd apps/android/kelo
./gradlew testDebugUnitTest         # unit tests (JDK 17)
python3 ../tools/strings_check.py   # every language has every string, placeholders match
```

### Desktop

```sh
# the text of all apps is apps/shared/strings (check: python3 apps/android/tools/strings_check.py); the desktop reads it as Compose resources, built with the app
cd desktop && ./gradlew test            # shared Android tests + desktop tests
./gradlew run                           # start the app
```

`apps/desktop/tools/mock_server.py` is a pretend server for trying the apps without a real one.

### iOS

```sh
cd apps/ios/KeloCore && ./test.sh
cd apps/ios/KeloUI   && swift build && ./test.sh
```

### Web

```sh
cd web && npm ci && npm run build
```

### Whole stack (Docker)

See `README.md` → *Install*. `compose.yml` builds the images locally.

## Pull requests

- Keep each pull request to one topic, with tests for new behaviour.
- Describe what changed and why, and how you checked it (commands run, screenshots from the mock server).
- CI must pass.
- By opening a pull request you confirm it follows the ground rules above.

## Name, logo and mascot

The code is licensed under the AGPL; the **Kelo name, the Kelo logo and the Kelo mascot character are not**. You may
refer to Kelo by name to describe your contribution or a compatible project, but a fork you distribute must use its own
name and artwork, and must not suggest that it is the official Kelo. The connector icons in
`server/src/assistant/connector_icons/` are trademarks of their owners and are not covered by Kelo's licence either.

## Code of conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
