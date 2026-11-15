# Security policy

## Reporting a vulnerability

Please report security problems privately by e-mail to **support@kelo.chat** with "SECURITY" in the subject line.
Do not open a public issue, discussion or pull request for a vulnerability.

If GitHub private vulnerability reporting is enabled on this repository, you may use **Security → Report a
vulnerability** instead.

Please include:

- what is affected (server, Android, iOS, desktop, web app, installer, website) and the version or commit;
- steps to reproduce, or a proof of concept;
- what an attacker could do with it;
- whether you want to be credited, and under what name.

Please do not access or change other people's data, run denial-of-service tests, or test against servers you do not own.
Test against your own Kelo server or the mock server in `desktop/tools/mock_server.py`.

## What to expect

- Kelo is a solo project maintained alongside a full-time job. Security reports are handled before anything else, and we
  confirm receipt as soon as we can.
- We send an assessment as soon as possible and keep you updated until it is fixed.
- We ask you to keep the issue private until a fix is released, or until **90 days** after your report, whichever comes
  first. We will agree on a different timeline with you when needed.
- Fixed issues are listed in `CHANGELOG.md` and, where appropriate, in a GitHub security advisory, with credit if you
  want it.

## Supported versions

Kelo is self-hosted. Security fixes go into the latest release only; please update with `kelo update` (or by running
the installer again) before reporting.

## Scope notes

- Your own server's configuration (open ports, reverse proxies, your domain) is your responsibility, but we welcome
  reports where Kelo's defaults or documentation lead people into an unsafe setup.
- Problems in a third-party service, model provider or CLI that Kelo connects to should be reported to that vendor; tell
  us too if Kelo should defend against it.
