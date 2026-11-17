# Talk to Kelo from a messenger

Kelo can also be reached from a messenger on your phone: Telegram, Discord and Slack. The chat is the same
conversation as in the Kelo app: what you write there shows in the app, and the reply shows in both.

## Telegram

1. In Telegram, open **@BotFather** and send `/newbot`. Pick a name and a user name that ends in `bot`.
2. BotFather gives you a token that looks like `123456:ABC-...`. Keep it to yourself: whoever has it controls the bot.
3. In the Kelo app open **Connectors -> Telegram** and paste the token (or ask Kelo in the chat to connect Telegram). Kelo checks it
   with Telegram before it saves it.
4. Kelo then writes a message in the app with a link `https://t.me/<your bot>?start=<code>` and a 6-digit code. Open the link on
   your phone, or send the code to the bot, within 10 minutes. The first Telegram account that does this is the only one that can
   talk to Kelo. Everyone else gets no answer at all.

### Groups (Telegram)

Kelo can also answer in a group, under four rules:

1. **Only when called.** In a group Kelo answers only to an @mention of the bot or a reply to one of the bot's messages. What
   else it reads is your choice in Telegram: with **Group Privacy** ON in BotFather (`/mybots` -> your bot -> Bot Settings ->
   Group Privacy; ON is the default) and the bot not an admin of the group, Telegram hands the bot only mentions, replies and
   commands, so Kelo sees only what was said to it. Make the bot an **admin** of the group (or turn Group Privacy off) and Telegram
   hands it the whole talk: Kelo then reads the recent talk as context for its answers, but still answers only when called.
2. **Nothing private.** What Kelo says in a group is read by everyone there. It has only web search, page reading and the weather
   there, no access to your mail, calendar, files, contacts, memory or money, and every reply is checked by the privacy guard
   before it is sent. If a reply would hold something private, the group gets "That is private, so I'll show it to the owner in the
   app instead." and you get a line in your own chat with the bot saying which kind of thing was held back.
3. **No cards in a group.** Allow / Deny cards and fill-in cards never appear in a group. A question that only you can allow is
   denied on the spot and you are told in your own chat with the bot (and the app).
4. **Only groups you added it to.** Add the bot to a group yourself (the group's info -> Add members -> your bot), as the paired
   account. Kelo then writes "I can now be called in the group ..." in your chat with the bot. A group that somebody else added the
   bot to is ignored without any reply. Other members of a group you added it to can call Kelo; the rules above are what protect you.
   Add the bot again to a group it was in before you paired it, because Telegram only tells the bot about the moment it joins.

Kelo keeps the last 20 lines it was handed in a group (its own replies included) so it can follow a conversation.
Ask Kelo "which groups do you answer in" or "stop answering in <group>"; removing the bot from the group also forgets the group.
Photos, files and voice messages in a group get the answer "In groups I only read text."

#### Other bots in a group

Kelo's reply is posted in the group, so when you ask it to ask another bot or person there ("ask @some_bot ..."), it writes the
question in its reply with their @name. That is its own reply, not acting for anyone; it still sends nothing to other chats or
people outside the group, no mail, purchases or sign-ups. For another bot to hear it, turn on Telegram's bot-to-bot communication
mode for both bots in BotFather. Messages from other bots are read like anyone's: kept as context, and answered when they mention
Kelo or reply to it. Kelo adds no loop guard or reply cap for bots; keeping two bots from answering each other is up to you.

### What works

- Text, photos and files (up to 20 MB, Telegram's own limit) in a private chat with the bot.
- Replies, with answer buttons under a question that has a few choices.
- Allow / Deny buttons for an ordinary approval. Payments, card or PIN entry, logins, keys and similar questions are never
  answered in Telegram: the message tells you to open the Kelo app.
- Fill-in cards (passwords, keys) are never taken in Telegram. Do not send a key or password to the bot: Kelo does not keep such a
  message and tells you to use the fill-in card in the app.
- `/new` starts a new conversation, `/stop` stops the reply that is running.
- Notifications: ask Kelo "send my notifications to Telegram" (off by default; ask again to turn it off).

### What does not work

- Channels, and groups the bot was not added to by you (ignored without a reply). Voice messages, stickers, video notes,
  locations and contacts.
- Another program that reads the same bot. A bot can have only one reader (a webhook or one `getUpdates` loop): Kelo is that
  reader, and when it starts it removes any webhook the bot had. If another program reads it too, Kelo tells you; make a second bot
  for the other program.

### Stopping it

- Connectors -> Telegram -> remove, or ask Kelo to disconnect Telegram.
- Or send `/revoke` to @BotFather: the old token stops working and Kelo tells you. Connect again with the new token to pair again.

## Discord

1. Open <https://discord.com/developers/applications> -> **New Application** -> **Bot** -> **Reset Token** and copy the token.
2. On the same Bot page turn **Message Content Intent** ON. Without it Discord shows the bot no message text; Kelo then says so
   and stops.
3. **OAuth2 -> URL Generator**: tick the scope `bot` and the permissions Send Messages, Read Message History and Add Reactions,
   open the URL it makes and add the bot to a server you own (a private server just for you is fine).
4. Paste the token in **Connectors -> Discord** (or ask Kelo in the chat). Kelo posts a 6-digit code in the app. Send it to the bot
   in a **direct message** within 10 minutes (Discord has no link that opens the bot with a code). That account is the only one that
   can talk to Kelo in direct messages.

It works like Telegram: text, photos and files (up to 25 MB) in the direct message, answer buttons under a question that has a few
choices, Allow / Deny buttons for an ordinary approval, and the same groups rules for **servers you own**: a server counts as
"added by you" when its owner is the paired account. Mention the bot (or reply to one of its messages) in a channel to call it; it
reads the recent talk of channels it can see as context (limit that with the channel's permissions for the bot). A
server somebody else owns is ignored. Kelo reads the servers the bot is in when it connects, so a server you added before pairing
is used as soon as you pair. The bot never pings anyone (@everyone and mentions in its replies are switched off).

Kelo reads Discord through the Gateway (a websocket) and answers through the REST API; there is no webhook to set up. If Discord
refuses the bot (a wrong token, or the Message Content Intent off) Kelo tells you once and stops.

## Slack

The Slack connector covers both jobs with one Slack app: reading and posting as the owner, and chatting with Kelo as a bot over Socket Mode (a websocket, with no public address or webhook to expose).

1. Make an app at <https://api.slack.com/apps> -> **Create New App** -> **From scratch**.
2. Under **Socket Mode**, turn it on and make an app-level token with the scope `connections:write` (it starts with `xapp-`).
3. Under **OAuth & Permissions**, add `search:read`, `channels:history`, `channels:read`, `groups:history`, `im:history`, `users:read`, `chat:write` to **User Token Scopes**, and `chat:write`, `im:history`, `channels:history`, `groups:history`, `channels:read`, `groups:read`, `reactions:write`, `files:read` to **Bot Token Scopes**.
4. Under **Event Subscriptions**, turn it on and subscribe to the bot events `message.im`, `message.channels`, `message.groups` and `member_joined_channel`. Turn on **Interactivity & Shortcuts**, and under **App Home** allow messages in the Messages tab.
5. Press **Install to Workspace** and paste the three tokens into **Connectors -> Slack**:
   - the **User OAuth Token** (`xoxp-`), which reads, searches and posts as the owner;
   - the **Bot User OAuth Token** (`xoxb-`), which Kelo's own bot uses for chat;
   - the **App-level token** (`xapp-`), which opens Socket Mode.

### How it works

- **Pairing by direct message:** after the tokens are saved, the server posts a 6-digit code in the app (valid 10 minutes). Send it to the bot in a direct message in Slack. From then on only that Slack user account talks to Kelo there.
- **Channels (groups):** invite the bot to a channel (the channel's details -> Integrations -> Add apps -> your bot). A channel counts only when the paired owner invited the bot. There the bot answers only when @mentioned or replied to in its own thread, with the same group rules as Telegram and Discord (public tools only, no access to private data, no cards). Replies in a channel go into the message's thread; direct messages are plain messages.
- **Older connections keep working:** a Slack connection saved before this change holds a plain `xoxp-` user token. It continues to work for searching, reading and posting as the owner, and the chat simply does not start (no "stopped" notification). Connecting again with all three tokens turns the chat on.

