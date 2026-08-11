// static/sw.js
// ============================================================
//  Only cache public assets & pages at install time.
//  Protected routes (requiring login) are cached on‑demand.
// ============================================================

const CACHE_NAME = 'oxsmart-v3';  // ⚠️ increment this on every deploy
const OFFLINE_PAGE = '/offline.html';

// ----- PUBLIC ROUTES (no login required) -----
const PUBLIC_ROUTES = [
  '/',
  '/login',
  '/signup',
  '/offline',          // if you have a dedicated offline page
];

// ----- STATIC ASSETS (always cache) -----
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/js/offline.js',
  '/static/js/main.js',
  '/static/js/app.js',       // if present
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  // Add any other fonts, images, etc.
];

// Combine into pre‑cache list
const PRECACHE_URLS = [...PUBLIC_ROUTES, ...STATIC_ASSETS];

// ===== INSTALL =====
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('📦 Pre‑caching public assets');
        return cache.addAll(PRECACHE_URLS);
      })
      .then(() => self.skipWaiting())   // force activation
  );
});

// ===== ACTIVATE =====
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            console.log('🗑️ Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())  // take control immediately
  );
});

// ===== FETCH =====
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);

  // Only handle GET requests and same‑origin resources
  if (request.method !== 'GET' || url.origin !== location.origin) {
    return;
  }

  // ----- API requests – stale‑while‑revalidate -----
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache => {
        return fetch(request)
          .then(networkResponse => {
            cache.put(request, networkResponse.clone());
            return networkResponse;
          })
          .catch(() => cache.match(request));
      })
    );
    return;
  }

  // ----- Pages & static assets (HTML, CSS, JS) -----
  event.respondWith(
    caches.match(request)
      .then(cachedResponse => {
        if (cachedResponse) {
          // Stale‑while‑revalidate: update in background
          event.waitUntil(
            fetch(request)
              .then(networkResponse => {
                return caches.open(CACHE_NAME).then(cache => {
                  cache.put(request, networkResponse.clone());
                  return networkResponse;
                });
              })
              .catch(() => {})   // ignore network errors
          );
          return cachedResponse;
        }

        // Not in cache – try network, then cache for next time
        return fetch(request)
          .then(networkResponse => {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
            return networkResponse;
          })
          .catch(() => {
            // If the request is for a page (HTML), show offline fallback
            if (request.headers.get('accept').includes('text/html')) {
              return caches.match(OFFLINE_PAGE);
            }
            // For other assets, return a simple error response
            return new Response('Offline', { status: 503 });
          });
      })
  );
});