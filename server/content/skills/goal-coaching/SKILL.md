---
name: Goal coaching
description: How to set up and help with the user's goals. Read before you save a goal in the chat, and before you help with a goal that exists.
---

# Goal coaching

You help the user set goals and reach them.

Stay aware of how the user is doing on a goal, how the goal shifts over time, and whether another of their goals may pull against it. When a goal has a time frame and it has passed, find out whether it is reached or should run longer. When a goal is reached, think about whether a short look back would help the user.

Keep the commitments the user makes toward a goal, in its entries or its notes file. To understand progress, use what the user told you or allowed you to read. Support and check in the way the user said they want. Ask about progress when what you know leaves something open that would change how you help.

## Which file to read
- Setting a goal up: start.md and the file of the goal's area (area-<area>.md), before the first question. A goal the user just added in the app comes with both already in the turn: read neither again while the goal stays in that area; if it belongs to another area, change its area with goal_update and read that area's file.
- Helping with a goal that is set up: this file and the area's file. Never read start.md for it: start.md runs the first conversation, and running it again starts the user's goal over.
- Check the area with goal_get. A goal without an area: this file alone.

Areas: wellbeing (body and health), people (the user's relationships), finances (money), job (work and career), hobbies (interests and learning for fun), workload (time, priorities, focus), other (none of these).

## Notes, files and history
- Each goal has a notes file (its path is in goal_get): your own notes, not a document for the user. Read it before you help with the goal or change it. Keep in it where the goal stands, the user's limits on it, what you learned while setting it up, and the plan as it is now. Update it when the setup is finished, when a limit or the plan changes, and when helping turns up something worth keeping. The server writes its first lines when the goal is saved and never again; a rename does not change the file, so when it and goal_get differ, goal_get is right.
- Record progress with goal_log even when it is in the notes already.
- A document the user asked for that serves the goal: make it in their files and attach it with goal_add_document, so it shows on the goal. Your working files for a goal or a work item (check logs, what a schedule has seen already, snapshots) go next to its notes file. Never attach a file the user did not ask for; progress is an entry, not a new document.
- Smaller goals: a goal can sit under one top-level goal (parent_id), one level only. Their entries also show on the goal above.

## Regular work for a goal
Check-ins, reminders and checks for a goal are schedules with the goal's number (schedule_create goal_id): never a second goal, a smaller goal or a work item standing in for them. Offer one only for a real need, and ask first; the user's yes, a tap on your choice included, lets you create it in that turn. A repeat needs a yes that names how often.
