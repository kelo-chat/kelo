# Server and updates

Kelo is a server you run yourself, on your own computer (Mac, Windows or Linux). All your chats, memory, files and keys stay there. The apps are the way in.

## Installing

Run the one-line installer from kelo.chat (a shell command on macOS and Linux, a PowerShell command on Windows). On Windows and, for now, on macOS it needs Docker. A guided setup checks the machine, asks for a size, starts Kelo and then connects a phone. The AI model is chosen later in the app: it asks for a model and its key, and keeps asking until one is set.

## Size

The setup offers Recommended (about 4 GB memory, 4 CPUs, 20 GB disk), Minimum (2 GB, 2 CPUs, 10 GB) or No limit, which shows the machine's real numbers. You can change it later.

## Reaching the server from your phone

The app needs an https address. The ways are: a name.kelo.chat address (offered for now; availability and terms may change), a private Tailscale network, Tailscale Funnel (public), your own domain through Cloudflare, your own domain direct (ports 80 and 443 open) or behind your own proxy. A copy uses one way at a time; picking another turns the old one off.

## Updating

Updates come from the app: when a newer server release exists the app offers it, and the installer sets up an updater that pulls signed official images. You can also turn on automatic nightly updates. A server built from the source files updates with a command on the server machine instead. The server stays compatible with the latest released apps, and when an app and server are too far apart the app tells you which one to update.

## More than one server

One computer can host several Kelo servers, each with its own address, token, chats, files and keys. Each needs its own memory and disk. They share the images and the updater, and updating the main one updates them all.

## Disk and limits

Kelo keeps its own data bounded where it grows without end (logs, caches, history of runs); what you own, such as your files and memories, is not limited by Kelo itself. The size you chose caps what all of Kelo's containers may use together.

## AI models

You pick the model in the app and give it a key. Kelo uses one model for everything it does. Keys can be held in a separate key container, see the keychain page.

## When something does not work

If the app says the server is too old, or the app is too old, update the one it names.
