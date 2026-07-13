// Minimal service worker — its only jobs are (1) satisfy the PWA install criterion (a fetch handler)
// and (2) stale-while-revalidate the hashed static assets so the shell opens instantly. It NEVER
// touches /api: the chat stream is a POST + Server-Sent Events, and a caching SW must not sit in that
// path, so those requests fall straight through to the network.
const CACHE = "chimera-shell-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return; // POSTs (incl. the chat stream) pass through
  if (url.origin !== self.location.origin) return; // only our own origin
  if (url.pathname.startsWith("/api")) return; // never cache the API (SSE/streaming) — let it hit net

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
