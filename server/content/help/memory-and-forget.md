# Memory and forget

Kelo remembers what will matter next time: facts about you, preferences, plans, decisions and promises with dates. It keeps them in its own files on your Kelo server, not in the AI provider's account.

## What is kept

Short notes about you and about how Kelo works for you, a page for each person you mention (name, other names, relation), and a log of what happened on each day. Routine steps and tool output are not kept. Kelo writes things down as it learns them and tidies them in a nightly pass, which keeps a backup and can be undone. Secrets such as passwords and keys are scrubbed from notes.

## How Kelo uses it

Each turn Kelo reads only the few notes that match your message, not everything. Your saved preferences (sizes, colours, address, defaults) decide matching choices, also on another site than the one you first said them for, unless you limited them. Kelo can say where a remembered fact came from, and where it is stored; ask "why do you think that?".

## Looking back

Kelo can search the journal of earlier days to answer "what did we do last Tuesday?".

## Correcting

Tell Kelo a note is wrong or has changed (a booking was cancelled, you moved). It fixes the old entry instead of adding a conflicting one.

## Forget

Ask Kelo to forget something and it does a cleanup across the whole assistant, not an edit of one note. First it only looks: it finds every place the topic lives (memory notes, day logs, summaries, reminders, schedules, watchers, things it created) and shows you the list. Then it asks once on a card. After you confirm it deletes, checks again, and says "forgotten" only when nothing is left. It will not write that information down again.

Kelo also tells you what it cannot erase: copies in outside services, other people's copies, and what sits in the chat history itself. The messages in the chat stay unless you clear the chat in the app.

## Data that leaves

Kelo sends your private data only where a task needs it and only to the person or service the task is for. A guard checks outgoing data against what you asked, and a model provider you chose sees what is in the current request.
