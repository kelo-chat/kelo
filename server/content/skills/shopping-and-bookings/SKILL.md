---
name: Shopping and bookings
description: Buy, order, reserve or book on a website: repeat buys (task_allow purchase fields) and what to do when an order goes wrong.
requires: [browser]
---

# Shopping and bookings

The user wants something bought, reserved or booked. You do it in your own browser, and the user approves the money step.

## Before you open a site
- Collect every choice the order needs in one message: what exactly, its options, how many, when, where it goes, and a price limit if the user has one. Skip what the request already says.
- If the user named a site, use it. Otherwise pick a well-known one for their country and say which.

## Run it as a job
- Finding options takes a few pages: do that yourself and offer them as a choice card (`quick_replies`, options with cards: picture, price · arrival, one recommended).
- Once the choices are known, the buying or booking itself goes to `delegate` (kind browser, `url` the site's home page): the item and chosen option, quantity, the shipping profile and card the user prefers (from memory), the price limit, and "follow the shopping skill, stop at confirm_purchase". Tell the user in one line that it started and that they can keep talking. Do not run the session in this reply.
- The job cannot ask the user. When it needs a new choice (an option sold out, a price that changed, a login or a code) it reports `needs_input`; you ask the user (a choice card when there are options), then `resume` the job with their answer as `note`. Payment still goes only through `confirm_purchase`: the user's approval card reaches them from the job.

## On the site
1. Start from the site's home page and use its own search box and links; never build search or product addresses. Do not reload or reopen pages in quick succession. On an access-denied page, stop at once: on the first block in a job, report blocked with retry_after_min=10 to retry automatically once in 10 minutes; on a second block, report needs_input asking whether to clear that site's cookies and retry; if blocked a third time, report blocked and offer the live view or another shop. `browser_open` the site and find the item. Compare two or three options only if the user asked for the best or cheapest. When looks matter (clothes, furniture, rooms), show each option's picture (a choice card, or `show_picture` url).
2. `begin_order(site, what)` with what the user asked for. Until you pay, the steps that only prepare this order on that site (cart, checkout, next step, options, quantity, delivery, coupon, the shipping form) go through without an approval card. Passwords, sign-ups, account changes, other sites, messages, deletions and any payment still ask.
3. If the site needs an account: `vault_status`; with a saved login, `browser_login`; without one, `request_login` for that site: the fill-in card appears in the chat and you go on by yourself once it is saved.
4. The address: if the account already has one saved, select it on the page and keep its text for `ship_to`. Otherwise `browser_fill_shipping` with the user's shipping profile (the default one unless they named another; `shipping_profiles` lists them). With no profile and nothing saved on the site, `ask_shipping` once: a form in the chat, prefilled with what you remember of the user's name, phone and address so they can check it. Never type an address you only remember without that form.
5. Fill the order up to the last button before payment or confirmation. Read the page again (`browser_read`) and note each item with its options and quantity, the address, the total with fees, and the cancellation terms.
6. `confirm_purchase` once with `items`, the total, `ship_to` (when the site's saved address is used), the coupon and the card: it is the one card the user answers. Press the final button only after APPROVED.
7. After APPROVED and the final button, read the confirmation page and call `purchase_result` with its order number, charged total, items and cancel terms. A charge that differs from what was approved: cancel at once only if the server says cancelling is free, if a fee applies ask the owner (never cancel on your own), and tell them plainly what was approved, what was charged and the next option.
8. Report what happened with the confirmation number copied from the page, and anything the user must do next.

## Paying
- Prefer what the site already holds for the user (account balance, points, a card saved on the site).
- Otherwise one of the user's cards (`wallet_cards`): the one the user named, or ask which. With no usable card, `request_card`: a secure card entry appears in the chat and you go on once the card is added.
- When a payment needs the user's approval, the result says why (a reason code in brackets). Repeat that reason only; never guess about the user's settings.

## Comparing prices
- Prices, fees and promotions come from the seller's own live page (up to the step before paying when the total depends on options), not from search snippets or posts about them; say when you checked.
- Compare the total over the whole term: fees, penalties for leaving the current contract, discounts that need a card, a bundle or a minimum spend (show the price without them too), and cash gifts spread over the months.

## Buying on a repeat
- A purchase that should happen again and again is a schedule; one that should happen when something shows up (a cancelled seat, a restock, a price below a line) is a watcher or a polling schedule. At setup, ask everything in one message (what exactly, the most per time, how many times, how often, which card, which login, which shipping address), create it, then call `task_allow` once with the trigger, the action, the login and the purchase (`purchase.max_count`: 1 for a one-shot buy; `purchase.card`; `purchase.shipping`: the shipping profile's nickname). The card shows the card's own auto-pay limit next to the amount asked for; a purchase within the task's limit goes ahead even above the card's auto-pay limit, only for that task. Each run starts its order with `begin_order` and ships to that profile. Tell the user that once they allow it, it signs in and buys on its own within that limit, they get a notice right after, and a one-shot task turns itself off after buying.
- Anything outside what was allowed (a higher total, another site or item, new terms, a card that only the phone can send) still asks at run time.

## When it goes wrong
- Something unavailable, a price that changed, a site that blocks automated use: stop and tell the user plainly, with one alternative if you have one. When the same item elsewhere is a fair alternative, prepare it there up to the step before paying first, then ask once with the difference in price and terms.
- Sold out or missed (a deal ended, the seats are gone): do not push a much pricier substitute; offer to watch for a restock or a cancellation and set it up on a yes.
- Never retry a payment that may have gone through. Check the account's order history first.
