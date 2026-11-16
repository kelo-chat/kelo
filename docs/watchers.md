# Watchers

A watcher checks one thing every so often without a model call and wakes Kelo only when its condition holds (a seat that opens, a
price that drops, a page that changes). It starts off; a dry run shows what it finds, and you approve turning it on. The full
contract is in the docstring of `server/src/assistant/watchers.py` and next to `serialize()` in `server.py`.

## Three ways to look, cheapest first

| level | what happens | used when |
|---|---|---|
| `light` | the server asks for the page itself, without cookies. It presents itself as the agent browser's own Chrome (same user agent, your language) and goes through your VPN or proxy when one is on | the default |
| `tab` | the same request is made *inside* Kelo's browser, from a background tab of that site: your sign-ins ride along, the request leaves by the browser's own route, and no cookie is ever read out of the browser. Same site only; at most 3 such tabs, each closed after 10 minutes unused | the watcher is `signed_in`, or its method is POST |
| `page` | a full page load in the browser (JavaScript runs). The real HTTP status and the browser's bot-check reading come with it | the watcher uses `use_browser` |

A watcher begins at the level its fields call for. When a look is **blocked** (HTTP 401 / 403 / 429 / 503, or a bot-check page such
as "Just a moment…") the same look is repeated at once one level up. The level that got past the block is kept for 24 hours, then
the watcher tries its starting level again. A block that a higher level got past is not an error and does not count toward the five
failures in a row that turn a watcher off. A POST never goes up to `page` (a page load is a GET). If the VPN is down, the check
fails and nothing is retried around it.

## A human check

If even the browser meets a check it cannot pass by loading the page, the watcher does **not** fail. It waits twice as long between
checks (then four times, at most) and goes back to normal after a clean look. Once a day it tells you: "*site* asks for a human check,
so watching '*name*' is paused there. Open the page in Kelo's browser view once to pass it." Kelo never uses a solving service, free or
paid.

## Signed-in pages and a site's own JSON

Most sites that show your own data (reservations, seats, balances) load it from an address of their own that returns JSON. Find it in
the browser (the page's network requests), then give the watcher that address as `url`, with `signed_in: true` when it needs your
sign-in. For an address that wants a POST, set `method: "POST"` and `body` (JSON or form text, at most 4 KB). `headers` (at most 8)
may be Accept, Content-Type, X-Requested-With, Referer, Origin or any `X-…` name; Cookie and Authorization are never accepted.

**A watcher only reads.** A POST whose address path or body names an action (order, pay, payment, checkout, purchase, buy, book,
booking, reserve, reservation, cancel, delete, remove, apply, submit, send, confirm and their Korean equivalents for paying, ordering, buying, booking, applying, cancelling, deleting and sending)
is refused. Reading words (search, list, query, seat, status, inquiry and the Korean words for looking up or searching) are fine, and a GET may use any address. Changing any
of these fields turns the watcher off until a new dry run, and turning it on still needs your approval; the card shows the method, the
address and the body.

## Not looking like a bot

The next check comes after the interval × a random 0.8–1.2 (never under 5 minutes). When a look says the condition now holds, one more
look follows 30 seconds later and only a second agreement wakes you, so a half-loaded page or a momentary "0 seats" stays quiet.
(The dry run does not confirm.)
