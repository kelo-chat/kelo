# Buying the moment a watched item shows up ("instant buy", backlog 71)

Status: design v1 (2026-11-14). Owner's choice: build all three parts at once (ready state + fast run + remembered steps).

## Today

The owner says "buy this as soon as it is back in stock". The agent then:
- makes a watcher (watchers.py) on the product page;
- asks ONE setup card (`task_allow`) for a purchase grant (schedule_grants.py `purchase`: site, what, the most per time and
  currency, the count, a monthly cap, optionally the card and the shipping profile).

When the watcher's condition comes true:
- one more look 30 s later confirms it (CONFIRM_DELAY);
- then a hidden handoff starts a normal agent turn (server.py `watch_fired` → `worker_handoff`), under the scope `watcher:<id>`;
- that turn signs in, builds the order step by step and pays with `confirm_purchase`, which the grant covers (no card for the owner).
It works, but every step is a model call of 7-12 s on the full chat prompt: a few minutes in all. A cancelled seat or a limited drop
is gone by then.

Why not rehearse the checkout first: a sold-out item cannot be put in a cart, so the real order steps can only be walked when the item
is there. So:

## A. Ready-to-buy ("ready to buy")

A watcher that has a purchase grant (scope `watcher:<id>`, kind purchase) is **armed**. Module `buy_ready.py`, table `buy_ready`
(watcher_id PK, url, tab, armed_at, last_kept_at, last_ok_at, signed_in (0/1/NULL), problems JSON, plan_id).

- **A parked tab**: the tab `buy:<watcher id>` is opened on the watcher's url when the watcher is armed, and kept open (BrowserSession
  tab keys; the tab survives like other task tabs). At the hit, the run starts on this tab, so the product page is already loaded and
  the site's cookies are warm.
- **Keep-alive** every KEEP_MINUTES (15) × random 0.8-1.2 while armed:
  - reload the parked tab (no model call);
  - read the page tree once and record whether the page shows a sign-in link and no account link (`signed_in` false / true; the same
    words test the order tools use for sign-up and account steps);
  - a page that needs a sign-in, when the grant scope also has a login grant for that site, gets a quiet `browser_login` through the
    same code path a run of the task uses (no card);
  - without a login grant, it is listed as a problem ("signed out of <site>").
  Keep-alive never clicks anything else.
- **Readiness**, checked when armed and at each keep-alive. Each failed check is a problem with a code:
  - `card_needs_phone`: the grant's card has no server copy, so the payment waits for the phone and is slower;
  - `no_card`: no card chosen and the wallet has no default auto-pay card for the site;
  - `no_shipping`: no shipping profile in the grant and none saved;
  - `signed_out`;
  - `cap_used`: the grant has no count or money left;
  - `browser_off`: no agent browser on this server.
- **Faster checks while armed**: the watcher's interval may be 2 minutes (not under; jittered as today) instead of the usual 5-minute
  minimum. The 30 s confirmation look is skipped for an armed watcher (the fast run looks at the page itself anyway).
- **API**:
  - `GET /api/watchers/{id}` gains `"buy": {"armed", "tab", "last_kept_at", "signed_in", "problems": [{"code", "text"}], "remembered": bool}`
    (null when not armed);
  - `POST /api/watchers/{id}/buy-check` runs a keep-alive now and returns the same object.
  Disarm when the watcher is turned off or deleted, or its purchase grant is used up or removed (the parked tab closes).

## B. The fast run

At the hit of an armed watcher, instead of the normal handoff, `start_fast_buy(watcher)` starts a turn:
- `source="buy:<id>"`, unattended, `owner="buy:<id>"` (the parked tab), scope `watcher:<id>` (so the grants cover it exactly as today);
- a short system prompt, FAST_PROMPT: what to buy (the grant's `what`), the cap left, the shipping profile and card by name, "you are
  on the product page; add it, go to checkout, choose the saved address, pay with confirm_purchase; stop and report if anything
  differs (another item, a higher price, an extra fee, a sign-up)";
- a small tool set: browser_read, browser_click, browser_type, browser_fill_shipping, browser_fill_card, browser_login,
  confirm_purchase, end_order;
- `max_steps` 25;
- the main model, with the same fallback rules.
When it ends, the usual payment notification and the run's report reach the owner (as today's handoff report). If the run stops without
paying, the owner gets one urgent notification with why (the run's last words).

## C. Remembered steps (replay without the model)

- **Recording**: during a fast run (or a normal run under a watcher's purchase grant) that ends with a covered payment, every
  successful browser action is recorded, in order:
  - open: the url path, with no query values;
  - click: the element's role and name from the tab's latest page tree, plus the page url path it was on;
  - type: only for a field that is not personal (a quantity, an option, a coupon field the agent typed into). Never a password, an
    address, a phone number, an e-mail or a card;
  - select: an option's name;
  - fill_shipping: by profile id;
  - fill_card: a marker only;
  - confirm_purchase: a marker, with the page url path.
  Stored as a plan per (site, product url path) in `buy_plans` (id, site, product_path, steps JSON, created_at, used_count,
  last_ok_at). Personal values never enter a plan.
- **Replay** at the next hit for that site and product (or the same site and a matching path pattern): `buy_replay.py` runs the steps
  on the parked tab with no model call. For each step:
  - read the tree;
  - find the one element whose role equals the step's role and whose name equals the step's name (after whitespace and case
    normalisation). If none, accept a single element whose name contains the step's name; zero or several → stop;
  - act through the BrowserSession methods (the same policy checks run).
  The confirm step is checked in code, not by the model, with the same gate `confirm_purchase` uses:
  - items = the grant's `what`, which must appear in the page text;
  - total = the amount read from the page by `purchase_total(text, currency)`: the money amount on the line that names the total
    ("Total", "Order total", …). When there are several candidates and they differ, it stops;
  - it must be within the grant's limits (`schedule_grants.purchase_check`).
  Then the card fill and the final click go through the existing purchase window.
- **Any stop** (an element not found, a step that did not change the page, an unclear total, a refused check) hands over to the fast
  run (B) from the page where the replay stopped, with a note of what the replay did. A plan that stopped twice in a row is dropped.
- A successful replay updates `used_count` / `last_ok_at`.

## Safety (unchanged rules, restated)

- Nothing pays without the purchase grant the owner approved at setup: site, what, per-time and monthly limits, count.
- The final payment still goes through the wallet's rules: pause, vault lock, and a card that needs the phone.
- Recording keeps no personal values. A plan is not a permission: a plan without a grant does nothing.
- The keep-alive never clicks anything but the sign-in of a login the owner allowed for that task.

## Apps

On the watcher's card and its page:
- a "ready to buy" line with the state:
  - ready / problems, each in plain words with what to do;
  - "kept signed in" and when it was last checked;
  - "remembers checkout steps for this site" when a plan exists;
- a "check now" button (`POST …/buy-check`).
