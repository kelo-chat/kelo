"""The server no longer serves a web app: Kelo is used from the phone app. Opening the server's address in a browser (a pairing link
someone opened on the phone, the page a sign-in returns to) shows this small page, whose button opens the Kelo app."""

from __future__ import annotations

from html import escape

from . import i18n
from .i18n import t

# Opens the installed app (its launcher screen). Chrome on Android follows intent: links on a tap.
APP_OPEN = "intent:#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;package=chat.kelo.app;end"

_STYLE = """body{font-family:-apple-system,system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;margin:0;padding:24px;
background:#fbfbfa;color:#1a1a1a}main{max-width:420px;text-align:center}p{line-height:1.55;word-break:keep-all;overflow-wrap:anywhere}
a.btn{display:inline-block;margin-top:18px;padding:12px 20px;border-radius:12px;background:#6366f1;color:#fff;text-decoration:none}
.muted{color:#6b6b70;font-size:14px}@media (prefers-color-scheme:dark){body{background:#141416;color:#f2f2f3}.muted{color:#a0a0a8}}"""


def page(title: str, text: str, *, button: str | None = None, note: str = "", auto_open: bool = False) -> str:
    """The page in the current language (`title`, `text`, `button` and `note` come already translated; the button defaults to
    "Open the Kelo app")."""
    button = t("Open the Kelo app") if button is None else button
    lang = escape(i18n.current())
    extra = f'<p class="muted">{escape(note)}</p>' if note else ""
    script = f"<script>setTimeout(()=>location.href={APP_OPEN!r},1200)</script>" if auto_open else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{escape(title)}</title><style>{_STYLE}</style></head>'
            f'<body><main><h2>{escape(title)}</h2><p>{escape(text)}</p><a class="btn" href="{escape(APP_OPEN)}">{escape(button)}</a>{extra}</main>'
            f"{script}</body></html>")


def home() -> str:
    """The server's home page, in the language of the request (built per request, never at import time)."""
    return page("Kelo", t("Kelo is used from the phone app."),
                note=t("If you got a link for connecting a new device, paste it as it is into ‘Paste link’ in the Kelo app, or scan the QR code."))


_tok = i18n.use(i18n.DEFAULT)
HOME = home()   # English, built at import time: kept only for older callers; use home() so the page follows the request's language
i18n.reset(_tok)
