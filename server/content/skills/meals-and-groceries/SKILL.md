---
name: Meals and groceries
description: Weekly meal plans, grocery lists by store section, leftovers; ordering groceries only with approval.
---

# Meals and groceries

The user wants meals planned or groceries sorted out.

## Ask once, then remember
- First time: diets, allergies, foods they dislike, household size, cooking time on weekdays, and a rough budget. Ask in one message.
- Note the user's answers with `remember` (their own words) and keep the longer profile in `notes/meals/profile.md`; read it before every plan. Note it again when the user changes anything. Never plan around an allergy you have not confirmed.

## Weekly plan
1. Plan the meals asked for (default: dinners for the week), sized to the household.
2. Reuse ingredients across meals so little is wasted: one roast becomes a second meal, half a cabbage appears twice. Plan one leftovers meal.
3. Ask what is already in the kitchen and use it first.
4. Show the plan as a short table: day, dish, cooking time. Save it to `notes/meals/<YYYY-MM-DD>.md`.

## Grocery list
- Combine amounts across meals, subtract what the user has, and group by store section: produce, meat and fish, dairy and eggs, pantry, frozen, other.
- Use the user's units and language. Mark what is only a suggestion.

## Leftovers
- When the user lists what is left, suggest meals that use it up, soonest-to-spoil first, and say how long it keeps.

## Ordering
- Never order or pay on your own. Offer to fill a cart; ordering follows the `shopping-and-bookings` flow and the user approves the total.
- A weekly order on repeat is a schedule: set it up only as that skill describes.

## Report
- Give the plan and the list. Say what you assumed, and what to check (stock, substitutions).
