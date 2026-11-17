# Keychain

The keychain is where Kelo keeps your passwords (website logins), service keys, connector tokens, payment cards and your AI provider keys. It lives on your Kelo server, sealed, and is shown in the app under the Keychain page.

## Nothing secret goes through chat

Passwords, keys, tokens, codes and card numbers are never asked for in the chat and never repeated by Kelo. When Kelo needs a login or a key it shows a fill-in card in the chat. Saving it is your consent, and Kelo goes on once you save. Kelo can sign in to a site with a saved login without the model ever seeing the password: it is typed into the page by Kelo's own code.

## Keys Kelo finds

If Kelo comes across a key while working, it shows as a numbered placeholder and Kelo can save it for you; it never asks you to copy it by hand.

## One-time codes

Codes from a text message or mail that a login needs can be entered into the page for Kelo without the model reading the code.

## Locked keychain

If the keychain is locked, Kelo tells you where to unlock it and does not work around it.

## Phone-held key (Keyguard)

In the app under My, Security, you can turn on Keyguard. The key that seals your passwords, keys and cards is then held by your phones, behind a fingerprint, face or device passcode, with a recovery code shown once. The server is locked until a phone unlocks it. Keyguard covers only passwords, keys and cards, not chats, memory or files.

## Key container

AI provider keys can be held in a separate container next to Kelo. The Kelo server then sends model requests without the key; the container adds it only for that provider's own address. A taken-over server could still make model calls but could not read a key out or send it elsewhere.

## Encrypted connection

The app talks to your server with end-to-end encryption on top of https, so a connection service in between (Cloudflare, for example) does not see the chat's content.

## Where Kelo stores what it learned about a service

Kelo's own notes on a service (what worked, its limits) are kept in plain notes without any secrets.
