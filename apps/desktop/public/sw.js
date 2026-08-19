// Minimal service worker — its only jobs are (1) satisfy the PWA install criterion (a fetch handler)
// and (2) make the shell open instantly from cache. It NEVER touches /api: the chat stream is a
// POST + Server-Sent Events, and a caching SW must not sit in that path, so those requests fall
// straight through to the network.
//
// The document is fetched network-first, and that is the whole lesson of this file. It used to be
// stale-while-revalidate like everything else — `cached || network` — which meant the app opened the
// PREVIOUS release's index.html, pointing at the PREVIOUS release's bundle, and only refreshed the
// cache in the background for next time. An installed app was therefore permanently one version
// behind: 0.48.0rc4 installed cleanly, the backend served the new bundle, and the window showed
// rc3's interface — with the bugs rc4 had fixed still on screen and no way to tell from inside the
// app that anything was stale. index.html is the map of which hashed assets to load, so caching it
// first pins the whole application to a moment in the past. Hashed assets under /assets are the
// opposite case: their name changes whenever their content does, so cache-first is always correct
// for them and there is nothing to revalidate.
const CACHE = "chimera-shell-v2";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) =>
  event.waitUntil(
    (async () => {
      // Drop every older cache, v1 included. Without this the old index.html survives the upgrade
      // and the offline fallback below would serve it — the same one-version-behind bug, wearing a
      // different hat.
      const stale = (await caches.keys()).filter((name) => name !== CACHE);
      await Promise.all(stale.map((name) => caches.delete(name)));
      await self.clients.claim();

      // Only when something was actually replaced: the window on screen is still running the old
      // bundle it loaded before this worker took over, and nothing else will ever tell it. A reload
      // at this exact moment is the difference between "the update applied" and "the update applies
      // the time after next".
      if (stale.length) {
        for (const client of await self.clients.matchAll({ type: "window" })) {
          client.navigate(client.url);
        }
      }
    })(),
  ),
);

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return; // POSTs (incl. the chat stream) pass through
  if (url.origin !== self.location.origin) return; // only our own origin
  if (url.pathname.startsWith("/api")) return; // never cache the API (SSE/streaming) — let it hit net

  // The document. Network first, cache only as the offline net.
  if (event.request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(event.request);
          if (fresh && fresh.ok)
            (await caches.open(CACHE)).put(event.request, fresh.clone());
          return fresh;
        } catch {
          const cache = await caches.open(CACHE);
          return (
            (await cache.match(event.request)) ||
            (await cache.match("/")) ||
            Response.error()
          );
        }
      })(),
    );
    return;
  }

  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      const network = fetch(event.request)
        .then((resp) => {
          if (resp && resp.ok) cache.put(event.request, resp.clone());
          return resp;
        })
        .catch(() => cached);
      return cached || network; // serve cache instantly, refresh in the background
    }),
  );
});
