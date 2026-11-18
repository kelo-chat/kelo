# Designed (HTML) mail

`email_send` takes an optional `html` argument next to the plain `body`. Without it a mail is plain text, as before. With it the
message is `multipart/alternative`: the plain text first, then the designed HTML.

## What is sent

- IMAP/SMTP mailboxes (Gmail, iCloud, Yahoo and the other app-password providers, Google sign-in): `EmailMessage` with
  `set_content(body)` and `add_alternative(html, subtype="html")`. An empty `body` is the HTML's own text (`mail_html.to_text`).
- Outlook / Microsoft 365 (Graph): one body only, so the HTML replaces the plain text (`contentType: HTML`). A reply with HTML is
  created as a draft reply (`createReply`), given the HTML body and recipients (`PATCH`), then sent (`send`).
- Both: the same 50,000-character cap as a plain body, applied to the HTML input before it is read.

## Sanitizer (`server/src/assistant/mail_html.py`)

- Allow-list tree built with `html.parser`: only the tags in `ALLOWED_TAGS` and the attributes in `ALLOWED_ATTRS` survive. Text is
  escaped again.
- Dropped with their content: `script`, `style`, `iframe`, `object`, `embed`, `noscript`, `template`, `textarea`, `select`, `svg`,
  `math`, `head`, `title`, media and canvas tags. Dropped as tags only: forms, inputs, buttons and any unknown tag (its words stay).
- Event handler attributes never match the allow-list. `href` keeps only `http`, `https` and `mailto` (control characters removed
  first). Inline `style` keeps `property: value` declarations only, and drops any value with `url(`, `expression`, `javascript`,
  `behaviour`, `binding`, `import`, a backslash or a brace.
- Remote images are never kept as images: an `<img>` becomes a link to its address (its `alt` as the text), so no mail is fetched and
  no tracking pixel goes out.
- If nothing survives, the send is refused before anything is signed in or sent.

## Approval and the approved text

- The card line is "Includes a designed (HTML) version" (translated in all 19 locale files). The card also shows the message (the
  plain text, or the words of the HTML when the plain text is empty) and the preview below (see Preview).
- The approved text is the plain body plus the HTML (`effects.effect_of`), so the digest that checks the approval, done-once and the
  outside-text and privacy rules all read the HTML too. A changed HTML asks the owner again.
- The owner cannot edit the body of a designed mail on the card (`approval_edit.editable` returns None when `html` is set): an edited
  plain text would not match the HTML sent with it.

## Preview (`mail_html.preview`, `GET /api/approvals/{id}/mail-preview`)

- The HTML never travels in the approval event. The event carries `mail` (`designed`, `preview` path, `pictures`, `links`) and the
  card's `lines` (To, Also to, Subject, Message). The notification's text (`notice_body`) is the summary line, the first 200
  characters of the plain text and "You can see the design in the Kelo app.", at most 400 characters.
- The route answers only while the card waits (404 otherwise, and for a plain mail). It returns the sanitized body that `clean` sends,
  wrapped in a fixed head: a `Content-Security-Policy` meta (`PREVIEW_CSP`: no script, no network, no frames, no forms, images off),
  `blocks` (the same words as plain blocks: headings, paragraphs, items with ordinal and depth, quotes, rules and table rows with
  cells), `cut` (the blocks stopped at 600 blocks or 300 pieces of text in one block; the document is never cut), `pictures` and
  `links`. Limit: 256 KB for the document (413 `too_big`). Headers: `Cache-Control: no-store`, the CSP, `nosniff`.
- Apps: Android and iPhone render the document in a web view with scripts, network and file access off, and every navigation refused;
  a tapped link asks before it opens in the browser (http, https and mailto only). The desktop app has no web view and draws the
  blocks itself, with a note that it shows the design in a simple form.
- The notification keeps Allow and Deny (an approval can be answered from it); the design is seen in the app. To answer designed
  mails only in the app, drop "allow"/"deny" for `mail` items in `api/approvals.py notify_actions`.

## Tests

`server/tests/test_mail_html.py` (sanitizer, multipart parts, plain-text derivation, refusal before sign-in, Mailboxes passing `bcc`
and `html` on, Graph payloads and bcc recipients, card line, digest and edit lock). Run it with
`cd server && PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_mail_html.py`.

`server/tests/test_mail_preview.py` (the card's lines and `mail`, the preview document is byte-identical to the body sent, its CSP
and head, the blocks, the security cases: scripts, images, unsafe links, remote loads, the route's auth, 404s, no-store and CSP
headers, the list without `_mail_preview`, the notification line, the locale texts). Same command with `tests/test_mail_preview.py`.
