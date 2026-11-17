# Browser

Kelo has its own browser, a separate Chrome on the Kelo server, which it drives itself to use websites: look at pages that need JavaScript, sign in, fill forms, order and book. For plain lookups it first uses web search and page reading, and opens the browser only when a page needs it.

## What it can do

Open a site, read the page, click, type, take a picture of the page, handle downloads and uploads, and sign in with a login you saved in the keychain. A human check (a "prove you are human" box) may be tried by Kelo itself in the page at most twice; Kelo never uses outside solving services. If it fails, the step is handed to you: you open the browser view in the app, pass the check, and Kelo goes on.

## Seeing and taking over

The app can show Kelo's browser while it works, and you can take over to sign in, solve a check or finish a step on the site, then hand it back.

## Buying and booking

Kelo does an order itself in this browser: it asks every missing choice once, prepares the order, then shows an approval card with what and how much before it pays. See the payments page.

## Privacy and safety

The browser has no route to your home network or to the server's own machine: it can only reach public internet addresses, through a checking proxy. Kelo's own sign-ins stay in this browser and are filled in by Kelo without showing the password to the model.

## VPN or proxy

Under Connectors, Network, you can connect a WireGuard VPN or a SOCKS5/HTTP proxy. Kelo's browsing and its web reading then go through it; AI model calls, your other connectors, notifications and your phone's connection stay direct. If the VPN is down, browsing is refused instead of going out directly.

## Android device

Separately, Kelo can drive an Android device or emulator that you run for it (tapping, typing, swiping in apps that have no usable website). It never drives your own phone this way. See the phone and devices page.
