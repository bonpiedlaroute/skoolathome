// Service worker: the app and the sheets stay available without network, and push
// notifications are shown. Answers given offline are kept by the page (outbox.svelte.ts):
// Safari has no background sync.

const VERSION = "v1";
const SHELL = `shell-${VERSION}`; // the app itself: index.html and its hashed assets
const DATA = `data-${VERSION}`; // API answers: last known sheets, progress, device

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL);
      const page = await fetch("/", { cache: "no-store" });
      if (page.ok) {
        const html = await page.clone().text();
        await cache.put("/", page);
        // Cache the scripts and styles the page loads, so the very first offline start works.
        const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
        await cache.addAll([...new Set([...assets, "/manifest.webmanifest", "/icon-192.png"])]);
      }
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) {
        if (key !== SHELL && key !== DATA) await caches.delete(key);
      }
      await self.clients.claim();
    })(),
  );
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) (await caches.open(SHELL)).put(request, response.clone());
  return response;
}

async function networkFirst(request, cacheName, cacheKey = request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      (await caches.open(cacheName)).put(cacheKey, response.clone());
    } else if (response.status === 401) {
      await caches.delete(DATA); // device revoked or not paired: forget its data
    }
    return response;
  } catch {
    const cached = await caches.match(cacheKey);
    if (cached) return cached;
    return new Response(JSON.stringify({ detail: "offline" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirst(request)); // file names change with their content
  } else if (url.pathname.startsWith("/api/cron/") || url.pathname.startsWith("/api/push/")) {
    return; // never cached
  } else if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(request, DATA));
  } else if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, SHELL, "/"));
  }
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Skoolathome", {
      body: data.body || "",
      tag: data.tag,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: data.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if ("navigate" in client) {
          const navigated = await client.navigate(target).catch(() => null);
          return (navigated || client).focus();
        }
      }
      return self.clients.openWindow(target);
    })(),
  );
});
