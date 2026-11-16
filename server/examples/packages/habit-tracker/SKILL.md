---
name: Habit tracker
description: Log the user's habits (walking, reading, water...) and look back on how they are going, with the Habits tab
requires: []
---
# Habit tracker

The Habits tab in the app shows the check-ins this package keeps on the server. The user can check in from the tab; you can too.

1. When the user says they did a habit ("walked today", "read 20 pages"), save it with `package_entry_add`
   (package `habit-tracker`, collection `checkins`, values `habit`, `day` as YYYY-MM-DD, and an optional short `note`).
   Use one of the habit names the tab uses when it fits: Walk, Read, Water.
2. When they ask how they are doing, read the entries with `package_entries` (newest first) and count by habit and day.
   Say plainly which habit slipped and suggest one small, concrete step. No lectures.
3. To undo a check-in, find its `_id` with `package_entries` and remove it with `package_entry_delete`.
4. Never invent check-ins the user did not report.
