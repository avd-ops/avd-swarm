/* avd-buddy service worker (docs/buddy-app.md section 5).
   Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
   Precaches the app shell only; network-only for /api/* and /.auth/*; deletes old caches on activate;
   never caches estate data; notifies clients with "Update ready, reload" instead of reloading mid-conversation. */

const CACHE_NAME = "avd-buddy-shell-v1";
const SHELL_ASSETS = [
  "./",
  "./index.html",
  "./app.css",
  "./app.js",
  "./fixtures.js",
  "./manifest.webmanifest",
  "./brand.json",
  "./assets/avd-ops.css",
  "./assets/avd-ops-shell.css",
  "./assets/theme.js",
  "./assets/brand/mark.svg",
  "./assets/fonts/ibm-plex-mono-latin-400-normal.woff2",
  "./assets/fonts/ibm-plex-mono-latin-600-normal.woff2",
  "./assets/fonts/schibsted-grotesk-latin-600-normal.woff2",
  "./assets/fonts/schibsted-grotesk-latin-700-normal.woff2",
  "./icons/avatar.svg",
  "./icons/favicon.svg",
  "./icons/favicon-32.png",
  "./icons/apple-touch-icon.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-maskable-512.png",
  "./icons/icon-monochrome-512.png"
];

function isBypassPath(pathname) {
  return (
    pathname.startsWith("/api/") ||
    pathname === "/api" ||
    pathname.startsWith("/.auth/") ||
    pathname === "/.auth"
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
  if (event.data && event.data.type === "CLEAR_ALL_CACHES") {
    event.waitUntil(
      caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
    );
  }
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }
  if (isBypassPath(url.pathname)) {
    // Network-only for /api/* and /.auth/*: never serve or store estate data in Cache API.
    event.respondWith(fetch(request));
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(request).catch(() => {
        if (request.mode === "navigate") {
          return caches.match("./index.html");
        }
        throw new Error("Offline");
      });
    })
  );
});
