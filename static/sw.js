// static/sw.js
const CACHE_NAME = 'oxsmart-v7';

// Add CDN resources and missing JS files
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/js/offline.js',
  '/static/js/db.js',
  '/static/js/sync.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  // 🔥 CDN resources for offline styling
  'https://cdn.tailwindcss.com',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('📦 Caching static assets...');
        return cache.addAll(STATIC_ASSETS);
      })
      .then(() => self.skipWaiting())
  );
});

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
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  // Only handle GET requests
  if (request.method !== 'GET') return;

  // For API calls – stale‑while‑revalidate
  if (request.url.includes('/api/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache => {
        return fetch(request)
          .then(networkResponse => {
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

  // For all other resources (including CDN) – cache first, fallback to network
  event.respondWith(
    caches.match(request)
      .then(cachedResponse => {
        if (cachedResponse) {
          // Return cached version and update in background
          event.waitUntil(
            fetch(request)
              .then(networkResponse => {
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

        // Not in cache – fetch and cache
        return fetch(request)
          .then(networkResponse => {
            if (networkResponse.ok) {
              const clone = networkResponse.clone();
              caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
            }
            return networkResponse;
          })
          .catch(() => {
            // Offline fallback for HTML pages
            if (request.headers.get('accept').includes('text/html')) {
              return new Response(
                `<html><body><h1>You are offline</h1><p>Please reconnect to use the app.</p></body></html>`,
                { status: 503, headers: { 'Content-Type': 'text/html' } }
              );
            }
            // For other resources, return a simple error
            return new Response('Offline', { status: 503 });
          });
      })
  );
});

// Handle CACHE_ALL_PAGES message from the main thread
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'CACHE_ALL_PAGES') {
    event.waitUntil(
      caches.open(CACHE_NAME).then(async (cache) => {
        for (const url of event.data.urls) {
          try {
            const response = await fetch(url, { credentials: 'include' });
            if (response.ok) {
              await cache.put(url, response);
              console.log(`📦 Cached: ${url}`);
            }
          } catch (err) {
            console.warn(`❌ Failed to cache ${url}:`, err);
          }
        }
        console.log('✅ All pages cached successfully');
      })
    );
  }
});