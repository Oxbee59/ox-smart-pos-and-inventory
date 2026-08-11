// static/sw.js
// ============================================================
//  Minimal, robust Service Worker – pre‑cache only static assets
// ============================================================

const CACHE_NAME = 'oxsmart-v4';  // ⚠️ increment this on every deploy

// ----- Only static assets (no HTML pages) -----
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

// ===== INSTALL =====
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('📦 Pre‑caching static assets');
        // Only cache static assets – no HTML pages that might redirect
        return cache.addAll(STATIC_ASSETS);
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
            // Cache the response (only if it's a success)
            if (networkResponse.ok) {
              cache.put(request, networkResponse.clone());
            }
            return networkResponse;
          })
          .catch(() => cache.match(request));
      })
    );
    return;
  }

  // ----- Pages & static assets -----
  event.respondWith(
    caches.match(request)
      .then(cachedResponse => {
        if (cachedResponse) {
          // Stale‑while‑revalidate – update in background
          event.waitUntil(
            fetch(request)
              .then(networkResponse => {
                // Only cache successful responses (not 302, 404, etc.)
                if (networkResponse.ok) {
                  return caches.open(CACHE_NAME).then(cache => {
                    cache.put(request, networkResponse.clone());
                    return networkResponse;
                  });
                }
              })
              .catch(() => {})
          );
          return cachedResponse;
        }

        // Not in cache – try network
        return fetch(request)
          .then(networkResponse => {
            // Cache the response if it's a success (200)
            if (networkResponse.ok) {
              const clone = networkResponse.clone();
              caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
            }
            return networkResponse;
          })
          .catch(() => {
            // If the request is for a page (HTML), return a simple offline message
            if (request.headers.get('accept').includes('text/html')) {
              return new Response(
                `<html><body><h1>You are offline</h1><p>Please reconnect to use the app.</p></body></html>`,
                { status: 503, headers: { 'Content-Type': 'text/html' } }
              );
            }
            // For other assets, return a simple error response
            return new Response('Offline', { status: 503 });
          });
      })
  );
});