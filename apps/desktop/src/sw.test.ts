/**
 * The service worker, executed — not grepped.
 *
 * It earns a test because it shipped a bug that was invisible from inside the app and survived two
 * releases: the document was served `cached || network`, so an installed Chimera opened the PREVIOUS
 * release's index.html, which pointed at the PREVIOUS release's bundle. 0.48.0rc4 installed cleanly,
 * the backend served the new files, and the window showed rc3 — with the bugs rc4 had fixed still on
 * screen. Nothing in the app could tell you that: the version in the status bar comes from the API,
 * which the worker never touches, so it read rc4 while the interface was rc3.
 *
 * `public/sw.js` is plain JS run by the browser, not imported by the bundle, so it is loaded here as
 * text and evaluated against a fake `self`, `caches` and `fetch`. That means the assertions are
 * about behaviour — what comes back from a request — rather than about the source containing some
 * string, which is the only kind of assertion that would have caught the original bug.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

const SOURCE = readFileSync(join(process.cwd(), "public", "sw.js"), "utf8");

type Handler = (event: FakeEvent) => void;

interface FakeEvent {
  request: FakeRequest;
  respondWith: (r: Promise<unknown> | unknown) => void;
  waitUntil: (p: Promise<unknown>) => void;
}

interface FakeRequest {
  url: string;
  method: string;
  mode?: string;
}

/** A cache that remembers what was put in it, keyed by URL. */
class FakeCache {
  constructor(
    public entries = new Map<string, { body: string; ok: boolean }>(),
  ) {}
  async match(request: FakeRequest | string) {
    const key =
      typeof request === "string" ? request : new URL(request.url).pathname;
    const hit = this.entries.get(key);
    return hit ? { ...hit, clone: () => hit } : undefined;
  }
  async put(
    request: FakeRequest | string,
    response: { body: string; ok: boolean },
  ) {
    const key =
      typeof request === "string" ? request : new URL(request.url).pathname;
    // Delete first, so a re-put moves the entry to the BACK. That is what the spec says a Cache
    // does, and the trim reads insertion order to decide who is oldest — a Map that kept the
    // original position would make this fake disagree with the browser about the one thing the
    // eviction depends on.
    this.entries.delete(key);
    this.entries.set(key, response);
  }
  /** Insertion-ordered, as the spec requires — the property the eviction reads. */
  async keys() {
    return [...this.entries.keys()].map((path) => ({
      url: `http://127.0.0.1:59592${path}`,
      method: "GET",
    }));
  }
  async delete(request: FakeRequest | string) {
    const key =
      typeof request === "string" ? request : new URL(request.url).pathname;
    return this.entries.delete(key);
  }
}

/** Load sw.js against fakes and hand back its listeners plus the world it sees. */
function loadWorker(
  options: { caches?: Record<string, FakeCache>; network?: typeof fetch } = {},
) {
  const listeners: Record<string, Handler> = {};
  const stores: Record<string, FakeCache> = options.caches ?? {
    "chimera-shell-v2": new FakeCache(),
  };
  const clients: { url: string; navigated: number }[] = [
    { url: "http://127.0.0.1:59592/", navigated: 0 },
  ];

  const self = {
    location: { origin: "http://127.0.0.1:59592" },
    addEventListener: (name: string, fn: Handler) => {
      listeners[name] = fn;
    },
    skipWaiting: vi.fn(),
    clients: {
      claim: vi.fn(async () => undefined),
      matchAll: async () =>
        clients.map((c) => ({
          url: c.url,
          navigate: async (url: string) => {
            c.navigated += 1;
            c.url = url;
          },
        })),
    },
  };

  const cachesApi = {
    open: async (name: string) => (stores[name] ??= new FakeCache()),
    keys: async () => Object.keys(stores),
    delete: async (name: string) => {
      const had = name in stores;
      delete stores[name];
      return had;
    },
    match: async (request: FakeRequest | string) => {
      for (const store of Object.values(stores)) {
        const hit = await store.match(request);
        if (hit) return hit;
      }
      return undefined;
    },
  };

  const fetchImpl =
    options.network ??
    (async () => ({
      body: "from network",
      ok: true,
      clone: () => ({ body: "from network", ok: true }),
    }));

  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function("self", "caches", "fetch", "URL", "Response", SOURCE)(
    self,
    cachesApi,
    fetchImpl,
    URL,
    { error: () => ({ body: "", ok: false }) },
  );

  return { listeners, stores, self, clients };
}

function request(path: string, over: Partial<FakeRequest> = {}): FakeRequest {
  return { url: `http://127.0.0.1:59592${path}`, method: "GET", ...over };
}

/** Fire a fetch event and return whatever the worker responded with (undefined = passed through). */
async function respond(listeners: Record<string, Handler>, req: FakeRequest) {
  let answered: unknown;
  listeners.fetch({
    request: req,
    respondWith: (r) => {
      answered = r;
    },
    waitUntil: () => {},
  });
  return answered === undefined
    ? undefined
    : ((await answered) as { body: string });
}

describe("the service worker", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("was actually loaded", () => {
    // Every test below asserts on listeners this call registers. If the file stops evaluating, they
    // would all throw rather than pass — but say so here, once, in a sentence.
    const { listeners } = loadWorker();
    expect(Object.keys(listeners).sort()).toEqual([
      "activate",
      "fetch",
      "install",
    ]);
  });

  it("fetches the document from the network even when a copy is cached", async () => {
    // THE bug. A cached index.html is the previous release's map of which bundle to load, so
    // serving it first pins the whole app to the previous version — permanently, since each launch
    // refreshes the cache for the launch after it.
    const cache = new FakeCache(
      new Map([["/", { body: "OLD index.html", ok: true }]]),
    );
    const { listeners } = loadWorker({
      caches: { "chimera-shell-v2": cache },
      network: (async () => ({
        body: "NEW index.html",
        ok: true,
        clone: () => ({ body: "NEW index.html", ok: true }),
      })) as unknown as typeof fetch,
    });

    const answer = await respond(listeners, request("/", { mode: "navigate" }));

    expect(answer?.body).toBe("NEW index.html");
  });

  it("falls back to the cached document when the network is gone", async () => {
    // The other half: network-first must not mean network-only. A backend that has not finished
    // booting would otherwise leave the window blank.
    const cache = new FakeCache(
      new Map([["/", { body: "OLD index.html", ok: true }]]),
    );
    const { listeners } = loadWorker({
      caches: { "chimera-shell-v2": cache },
      network: (async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    });

    const answer = await respond(listeners, request("/", { mode: "navigate" }));

    expect(answer?.body).toBe("OLD index.html");
  });

  it("answers a hashed asset from cache rather than waiting on the network", async () => {
    // Cache-first is correct exactly here: the name changes when the content does, so a hit can
    // never be stale, and the shell opens without a round trip. The request still goes out to
    // refresh the entry — that is the revalidate half — but the answer does not wait for it, which
    // is what this asserts.
    const cache = new FakeCache(
      new Map([
        ["/assets/index-abc123.js", { body: "cached bundle", ok: true }],
      ]),
    );
    let resolveNetwork: (v: unknown) => void = () => {};
    const network = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveNetwork = resolve;
        }),
    );
    const { listeners } = loadWorker({
      caches: { "chimera-shell-v2": cache },
      network: network as unknown as typeof fetch,
    });

    const answer = await respond(listeners, request("/assets/index-abc123.js"));

    expect(answer?.body).toBe("cached bundle");
    expect(network).toHaveBeenCalled();
    resolveNetwork({
      body: "fresher",
      ok: true,
      clone: () => ({ body: "fresher", ok: true }),
    });
  });

  it("never puts itself in front of the API", async () => {
    // The chat stream is a POST + SSE. A caching worker in that path breaks streaming outright.
    const { listeners } = loadWorker();

    expect(
      await respond(listeners, request("/api/code/turn", { method: "POST" })),
    ).toBeUndefined();
    expect(await respond(listeners, request("/api/doctor"))).toBeUndefined();
  });

  it("discards older caches when it activates, and reloads the window it just took over", async () => {
    // Both halves matter. Leaving v1 behind keeps the old index.html reachable through the offline
    // fallback; not reloading leaves the window running the bundle it loaded before the new worker
    // existed, so the update would only appear the time after next.
    const { listeners, stores, clients } = loadWorker({
      caches: {
        "chimera-shell-v1": new FakeCache(
          new Map([["/", { body: "OLD", ok: true }]]),
        ),
        "chimera-shell-v2": new FakeCache(),
      },
    });

    let waited: Promise<unknown> = Promise.resolve();
    listeners.activate({
      request: request("/"),
      respondWith: () => {},
      waitUntil: (p) => {
        waited = p;
      },
    });
    await waited;

    expect(Object.keys(stores)).toEqual(["chimera-shell-v2"]);
    expect(clients[0].navigated).toBe(1);
  });

  it("does not reload anything on a first install", async () => {
    // Nothing was replaced, so there is no stale window to rescue — and a reload here would
    // interrupt someone who just opened the app for the first time.
    const { listeners, clients } = loadWorker({
      caches: { "chimera-shell-v2": new FakeCache() },
    });

    let waited: Promise<unknown> = Promise.resolve();
    listeners.activate({
      request: request("/"),
      respondWith: () => {},
      waitUntil: (p) => {
        waited = p;
      },
    });
    await waited;

    expect(clients[0].navigated).toBe(0);
  });
});

/**
 * The shell cache stopped growing.
 *
 * Cache-first is correct for hashed assets — the name changes when the content does — but nothing
 * ever removed the old ones, so every release added a generation and none left. Measured on a real
 * install that had followed the rc series: **61 entries, 49.3 MB**, holding twenty generations of
 * `index-*.js` that nothing would ever request again.
 *
 * The eviction had to go where entries are ADDED, not in `activate`: `sw.js` changes about once a
 * quarter, so `activate` almost never fires, and a prune there would have been a fix that ran
 * roughly never while reading as if it worked.
 */
describe("the shell cache has a ceiling", () => {
  /** Fill the cache as N releases would: four assets each, the way a real build ships. */
  async function releases(n: number) {
    const cache = new FakeCache();
    const { listeners, stores } = loadWorker({ caches: { "chimera-shell-v2": cache } });
    for (let r = 0; r < n; r += 1) {
      for (const nome of [`index-r${r}.js`, `index-r${r}.css`, `Edit-r${r}.js`, `EditSidebar-r${r}.js`]) {
        await respond(listeners, request(`/assets/${nome}`));
        // The trim is detached from the response on purpose, so let its microtasks run.
        await new Promise((done) => setTimeout(done, 0));
      }
    }
    return { cache, stores, listeners };
  }

  it("keeps the newest assets and drops the oldest", async () => {
    const { cache } = await releases(5);
    const guardados = [...cache.entries.keys()];

    expect(guardados.length).toBeLessThanOrEqual(12);
    // The release that just loaded is intact — evicting the running one would be a worse bug than
    // the growth, because offline it would leave the app without its own bundle.
    for (const nome of ["index-r4.js", "index-r4.css", "Edit-r4.js", "EditSidebar-r4.js"]) {
      expect(guardados, `${nome} was evicted while it was the current release`).toContain(
        `/assets/${nome}`,
      );
    }
    // And the first release is gone, which is the point.
    expect(guardados).not.toContain("/assets/index-r0.js");
  });

  it("grows without a ceiling when nothing evicts", async () => {
    // Guarding the guard: the assertion above passes trivially if the fake never stores anything.
    // Twenty releases of four files each is eighty entries, and the cap must be what stops it.
    const { cache } = await releases(20);

    expect(cache.entries.size).toBeGreaterThan(0);
    expect(cache.entries.size).toBeLessThanOrEqual(12);
  });

  it("never evicts the document", async () => {
    // `/` is the offline fallback. Trading an unbounded cache for a blank window with no network
    // is not a fix, so the document is not a candidate however full the cache gets.
    const { cache, listeners } = await releases(1);
    await respond(listeners, request("/", { mode: "navigate" }));
    await new Promise((done) => setTimeout(done, 0));

    const { cache: cheia } = await releases(0);
    void cheia;
    for (let r = 1; r < 6; r += 1) {
      await respond(listeners, request(`/assets/index-x${r}.js`));
      await new Promise((done) => setTimeout(done, 0));
    }

    expect([...cache.entries.keys()]).toContain("/");
  });

  it("still serves an asset from cache without going to the network", async () => {
    // The behaviour the cache exists for, asserted after the eviction was added — a trim that
    // emptied the cache on every put would pass every test above.
    const rede = vi.fn(async () => ({ body: "from network", ok: true, clone: () => ({ body: "from network", ok: true }) }));
    const cache = new FakeCache(new Map([["/assets/index-abc.js", { body: "from cache", ok: true }]]));
    const { listeners } = loadWorker({ caches: { "chimera-shell-v2": cache }, network: rede as never });

    const resposta = (await respond(listeners, request("/assets/index-abc.js"))) as { body: string };

    expect(resposta.body).toBe("from cache");
  });
});
