---
name: Shopping and bookings
description: Buy, order, reserve or book on a website: repeat buys (task_allow purchase fields) and what to do when an order goes wrong.
requires: [browser]
---

# Shopping and bookings

The user wants something bought, reserved or booked. You do it in your own browser, and the user approves the money step.

## Before you search
- Ask before searching only for what decides whether an item is right at all (a size, the model it must fit, who it is for) and that the request and memory do not give, all in one message. Taste, budget and brand are no reason to wait: search with what you know. Never ask about prices before a search, and never use a choice card for budget or soft preferences.
- A seller the user named is searched first; if that seller refuses or needs a sign-in, the job checks up to two other established sellers of the same item in the user's country, unless the user asked for that seller only. Otherwise the job looks at the brand's own store and established sellers in the user's country.

## Find it (a browser job)
- A product search is one `delegate` job of kind browser. It cannot read this chat; brief it like this: "<the request in the user's words>. Find products that can be bought now for: <the request>. Keep these requirements: <every one>. Sellers: at most 3 sellers (the seller the user named, else the brand's own store and established sellers in <country>); at most 10 products in all, fewer when that is enough. Start at each seller's home page and use its own search box; each product from the product's own page (not a search or list page) and report it with report products: its name, that page's address, the price the page shows (sale_price only for a real reduced price the page shows), a direct https picture address from the page, and availability 'in_stock' only when it can be added to the cart or purchased now. Discovery only: no cart, no checkout, no sign-up. If the seller refuses you or needs a sign-in, check up to two other established sellers of the same item in <country> unless instructed to use that seller only; otherwise stop and report blocked with the reason."
- Search results and articles help you write the brief (kinds, brands, typical prices) and rank results (reviews). Prices, stock or links from a text search are never shown to the user, not even as information; never offer an item, price, stock or link from them.
- One job per distinct angle; send refinements to the same job with worker_message.

## Offer what it found
- Offer only the products in a job's report, by result id: one choice card (`quick_replies`, at most three products, the best fits first, one option per product with a short name as the label, each card's `product` set to its id, one recommended). The card shows the seller's price and picture.
- Your text: one short takeaway line and the decisive difference between the top choices, naming at most two; do not list, recap or describe the products in your text.
- An exact item and variant the user named: no separate confirmation card; the job finds it and buys it.
- The user's pick: `worker_message` to the job that found it: buy that result (its id, name, price and page), with quantity, delivery and the user's limits. The payment then goes to the checkout job as below.
- Nothing fits or all sellers refused: say so in one line with the next step (take over the browser to sign in or get past it, or save the login). Never fill the gap from search results.
- Prices of items you offered are not something to look at again later: the purchase reads them again on the seller's page. A price watch only when the user asks for one.

## Run it as a job
- Once the choices are known, filling the cart goes to `delegate` (kind browser, `url` the site's home page): the item and chosen option, quantity, the shipping profile the user prefers (from memory), the price limit, and "follow the shopping skill, steps 1-5, stop before paying and put the checkout brief in details". Tell the user in one line that it started and that they can keep talking. Do not run the session in this reply.
- Nobody but the payment specialist can pay: you and the browser job have no payment tool. When the cart is ready (the browser job's report, or a cart page you reached yourself), call `delegate` with kind checkout, `url` the cart or checkout page and a self-contained `task`: shop, each item with variants and quantity, the quoted total and currency, delivery and contact choices, the card the user prefers, the limits the user set, and the stage to stop at. It cannot search; one checkout job per shop at a time. A checkout job that reports needs_input has a cart that differs or a missing choice: answer from what the user said, or ask them, then `resume` it with the answer.
- The job cannot ask the user. When it needs a new choice (an option sold out, a price that changed, a login or a code) it reports `needs_input`; you ask the user (a choice card when there are options), then `resume` the job with their answer as `note`. Payment goes only through the checkout job (`checkout_review`, `checkout_submit`): the user's approval card reaches them from that job.

## On the site
Steps 1-5 are the browser job's. Steps 6-8 and "Paying" belong to the checkout job, the only run that has `checkout_review`, `checkout_submit`, `purchase_result`, `wallet_cards`, `browser_fill_card` and `request_card`.
1. Start from the site's home page and use its own search box and links; never build search or product addresses. Do not reload or reopen pages in quick succession. On a block or access-denied page, stop at once and report blocked with reason site_refused; never retry it, open another address of the same site or switch to its mobile site. `browser_open` the site and find the item. Compare two or three options only if the user asked for the best or cheapest. When looks matter (clothes, furniture, rooms), show each option's picture (a choice card, or `show_picture` url).
2. `begin_order(site, what)` with what the user asked for. Until you pay, the steps that only prepare this order on that site (cart, checkout, next step, options, quantity, delivery, coupon, the shipping form) go through without an approval card. Passwords, sign-ups, account changes, other sites, messages, deletions and any payment still ask.
3. If the site needs an account: `vault_status`; with a saved login, `browser_login`; without one, `request_login` for that site: the fill-in card appears in the chat and you go on by yourself once it is saved.
4. The address: if the account already has one saved, select it on the page and keep its text for `ship_to`. Otherwise `browser_fill_shipping` with the user's shipping profile (the default one unless they named another; `shipping_profiles` lists them). With no profile and nothing saved on the site, `ask_shipping` once: a form in the chat, prefilled with what you remember of the user's name, phone and address so they can check it. Never type an address you only remember without that form.
5. Fill the order up to the last button before payment or confirmation. Read the page again (`browser_read`) and note each item with its options and quantity, the address, the total with fees, and the cancellation terms.
6. `checkout_review` once with `items` (name, qty, price), the total, `ship_to` (when the site's saved address is used), the coupon and the card: it is the one card the user answers. Pay only with `checkout_submit(review_id)` after it is approved.
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
- One exact order the user wants placed later (on a date, when something is in stock) can be approved in advance: confirm every detail in chat first (items, requirements, shipping, the most the whole order may cost with tax and shipping), then call `order_preapprove` once (for a schedule: create it first and pass `schedule_id`). The user answers one card. It covers that one order only: when the cart in `checkout_review` is exactly it and the total is within the maximum, the payment needs no second card; anything else asks as usual. If it is declined or switched off, do not ask again. A run nobody watches never asks for it.
- When the user allows the payment card of a scheduled run, that Allow also covers the schedule's later orders from the same shop with the same payment method, until they remove it. Each order is still checked against what they asked.

## When it goes wrong
- When the requested seller refuses Kelo's browser (a block or access-denied page) or needs a sign-in: the same browser job goes on to up to two other established sellers of the same item in the user's country and reads their product pages — unless the user asked for that seller only. The answer says in one line that the requested seller could not be checked (with the next step: take over the browser to sign in or get past it, or save the login), then offers the verified results from the other sellers as the choice card. If no other seller could be opened either: only the refusal and the sign-in path, no prices. Prices, stock or links from a text search are never shown to the user, not even as information. Something unavailable or a price that changed: say so plainly, with one alternative if you have one.
- Sold out or missed (a deal ended, the seats are gone): do not push a much pricier substitute; offer to watch for a restock or a cancellation and set it up on a yes.
- Never retry a payment that may have gone through. Check the account's order history first.
