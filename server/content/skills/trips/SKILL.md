---
name: Trips
description: Plan a trip: day-by-day itinerary, packing list, bookings in the calendar, price and check-in watchers.
---

# Trips

The user wants a trip planned. You plan and prepare; money steps go through the `shopping-and-bookings` skill.

## Ask once
- One message with what the request does not already say: dates (or how flexible), budget, who travels (ages, mobility), pace (relaxed or packed), must-sees, and where they start from.
- Check MEMORY.md first for preferences you already know (food, hotels, how they get around).

## The plan
1. Look up each place with `web_search` and `web_fetch`: opening hours, closed days, ticket needs, and travel time between stops. Group stops by area so no day zigzags.
2. Write a day-by-day plan: morning, afternoon, evening, with travel time between stops and a rough cost per day. Leave slack; do not fill every hour.
3. Mark what you could not check ("hours unchecked") instead of guessing. Say when you looked.
4. Add a packing list for the weather, length and activities of the trip, short and grouped (documents, clothes, electronics, health).
5. Save the plan to `notes/trips/<place>-<YYYY-MM>.md` and say where it is.

## Pictures
- When you suggest hotels, rooms or places, or the user wants to see one, show pictures of the top options without being asked: find them on the official page (or a booking page) and use `show_picture` (url = its https image address); say where each is from.

## Calendar
- Offer to put flights, stays and booked tickets into the calendar (`gcal_create_event` or `phone_calendar_add`); ask first, then list what you added. Use local times at each place and say so.
- Check `calendar_events` for clashes with the dates before suggesting them.

## Watching
- For a price drop or check-in opening: `watch_add` on the seller's page, `watch_dry_run`, and tell the user what it will report and how often. Ask first.

## Never
- Never book or pay on your own. Anything bought, reserved or paid follows the `shopping-and-bookings` flow with the user's approval.
- Do not state a price or time you did not read on the page; say what is an estimate.

## Report
- Give the plan, the open questions, and the next step you suggest (what to book first, and any deadline).
