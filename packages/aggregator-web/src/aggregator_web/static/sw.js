const CACHE_NAME = 'aggregator-shell-v1';
const SHELL_URLS = [
  '/',
  '/static/styles.css',
  '/static/app.js',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// Assets that change between deploys — network-first so new code reaches users
// on a normal reload without manual SW unregistration.
const NETWORK_FIRST_PATHS = new Set(['/', '/static/app.js', '/static/styles.css']);

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // Ignore failures — app.js may not exist yet
      Promise.allSettled(SHELL_URLS.map((url) => cache.add(url)))
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  if (NETWORK_FIRST_PATHS.has(url.pathname)) {
    // Network-first: fetch fresh asset, update cache, fall back to cache offline.
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Cache-first for icons and manifest (content-addressed, never changes in place).
  const isCacheFirst =
    url.pathname.startsWith('/static/icons/') ||
    url.pathname === '/static/manifest.webmanifest';

  if (isCacheFirst) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});
