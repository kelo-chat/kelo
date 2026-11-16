// App-shell cache only. API traffic (tokens, chats) is never cached.
const CACHE = "assistant-shell-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.add("/")).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;

  if (req.mode === "navigate") {  // network first so a new deploy shows up, cached shell when offline
    e.respondWith(fetch(req).then((r) => { caches.open(CACHE).then((c) => c.put("/", r.clone())); return r; })
      .catch(() => caches.match("/")));
    return;
  }
  e.respondWith(  // hashed build assets / icons: cache first
    caches.match(req).then((hit) => hit || fetch(req).then((r) => {
      if (r.ok) caches.open(CACHE).then((c) => c.put(req, r.clone()));
      return r;
    })),
  );
});
