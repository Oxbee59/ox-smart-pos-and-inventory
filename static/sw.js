// static/sw.js
// ============================================================
//  Minimal, robust Service Worker – pre‑cache only static assets
//  + on‑demand page caching via postMessage
// ============================================================

const CACHE_NAME = 'oxsmart-v5';  // ⚠️ increment this on every deploy

// ----- Only static assets that actually exist -----
// (Add any other fonts, images, etc. that you have)
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/js/offline.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  // Add any other assets you need, e.g.:
  // '/static/fonts/...',
  // '/static/images/...',
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

// ===== MESSAGE HANDLER – cache protected pages on demand =====
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'CACHE_PAGES') {
    const urls = event.data.urls || [];
    if (urls.length === 0) return;
    event.waitUntil(
      caches.open(CACHE_NAME).then(async (cache) => {
        for (const url of urls) {
          try {
            // Fetch with credentials so the session cookie is sent
            const response = await fetch(url, { credentials: 'include' });
            if (response.ok) {
              await cache.put(url, response);
              console.log(`📦 Cached: ${url}`);
            } else {
              console.warn(`❌ Failed to cache ${url}: ${response.status}`);
            }
          } catch (err) {
            console.warn(`❌ Error caching ${url}:`, err);
          }
        }
        console.log('✅ All pages cached successfully');
      })
    );
  }
});